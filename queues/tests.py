import json

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse

from patients.models import Patient
from queues.models import Device, DeviceAssignment, IoTVital, Queue, TelemetryLog, Visit, VitalSign


class QueueDisplayNumberTests(TestCase):
    def test_number_starts_at_ten_and_never_duplicates(self):
        patient = Patient.objects.create(
            first_name="Queue",
            last_name="Number",
            national_id="9999999999999",
        )
        first = Queue.objects.create(visit=Visit.objects.create(patient=patient))
        second = Queue.objects.create(visit=Visit.objects.create(patient=patient))
        third = Queue.objects.create(visit=Visit.objects.create(patient=patient))

        self.assertEqual(first.display_number, "Q-10")
        self.assertEqual(second.display_number, "Q-11")
        self.assertEqual(third.display_number, "Q-12")
        self.assertEqual(len({first.display_number, second.display_number, third.display_number}), 3)


class IotTelemetryAssignmentTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.patient = Patient.objects.create(
            first_name="Demo",
            last_name="Patient",
            national_id="1234567890123",
        )
        self.visit = Visit.objects.create(patient=self.patient)
        Queue.objects.create(visit=self.visit, status=Queue.Status.MONITORING)
        self.other_visit = Visit.objects.create(patient=self.patient)
        Queue.objects.create(visit=self.other_visit, status=Queue.Status.MONITORING)
        self.device = Device.objects.create(
            device_id="DEV-001",
            api_key="secret",
            is_active=True,
        )

    def post_telemetry(self, payload):
        return self.client.post(
            "/api/iot/telemetry/",
            data=json.dumps(payload),
            content_type="application/json",
            HTTP_X_DEVICE_ID=self.device.device_id,
            HTTP_X_API_KEY=self.device.api_key,
        )

    def post_vitals(self, payload):
        return self.client.post(
            "/api/iot/vitals/",
            data=json.dumps(payload),
            content_type="application/json",
            HTTP_X_API_KEY=self.device.api_key,
        )

    def test_unpaired_device_cannot_send_telemetry(self):
        response = self.post_telemetry({"vitals": {"bpm": 88}})

        self.assertEqual(response.status_code, 409)
        self.assertEqual(TelemetryLog.objects.count(), 0)

    def test_telemetry_uses_active_device_assignment_without_visit_id(self):
        DeviceAssignment.objects.create(device=self.device, visit=self.visit)

        response = self.post_telemetry({
            "vitals": {
                "bpm": 92,
                "o2sat": 98,
                "bt": 37.1,
                "rr": 18,
                "sys_bp": 121,
                "dia_bp": 77,
            }
        })

        self.assertEqual(response.status_code, 200)
        log = TelemetryLog.objects.get()
        self.assertEqual(log.visit, self.visit)
        self.assertEqual(log.device, self.device)
        self.assertEqual(log.bpm, 92)

        vitals = VitalSign.objects.get(visit=self.visit)
        self.assertEqual(vitals.pr, 92)
        self.assertEqual(vitals.o2sat, 98)

    def test_mismatched_visit_id_is_rejected(self):
        DeviceAssignment.objects.create(device=self.device, visit=self.visit)

        response = self.post_telemetry({
            "visit_id": self.other_visit.id,
            "vitals": {"bpm": 101},
        })

        self.assertEqual(response.status_code, 409)
        self.assertEqual(TelemetryLog.objects.count(), 0)

    def test_complete_iot_vitals_moves_waiting_vitals_to_confirmation(self):
        waiting_visit = Visit.objects.create(patient=self.patient, final_severity=None)
        queue = Queue.objects.create(visit=waiting_visit, status=Queue.Status.WAITING_VITALS)
        DeviceAssignment.objects.create(device=self.device, visit=waiting_visit)

        response = self.post_telemetry({
            "visit_id": waiting_visit.id,
            "vitals": {
                "bpm": 92,
                "o2sat": 98,
                "bt": 37.1,
                "rr": 18,
                "sys_bp": 121,
                "dia_bp": 77,
            },
        })

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["vitals_complete"])

        queue.refresh_from_db()
        waiting_visit.refresh_from_db()
        self.assertEqual(queue.status, Queue.Status.WAITING_CONFIRMATION)
        self.assertIsNone(waiting_visit.final_severity)
        self.assertEqual(waiting_visit.triage_result.ai_severity, "GREEN")

    def test_iot_vitals_uses_active_device_assignment_without_patient_id(self):
        DeviceAssignment.objects.create(device=self.device, visit=self.visit)

        response = self.post_vitals({
            "device_id": self.device.device_id,
            "heart_rate": 92,
            "spo2": 98,
            "temperature": 37.1,
            "respiratory_rate": 18,
            "blood_pressure_sys": 121,
            "blood_pressure_dia": 77,
        })

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["success"])
        self.assertEqual(payload["visit_id"], self.visit.id)

        vital = IoTVital.objects.get()
        self.assertEqual(vital.patient_db_id, self.patient.id)
        self.assertEqual(vital.patient_identifier, self.patient.hn)

        log = TelemetryLog.objects.get()
        self.assertEqual(log.visit, self.visit)
        self.assertEqual(log.device, self.device)

    def test_iot_vitals_rejects_unpaired_device_without_patient_id(self):
        response = self.post_vitals({
            "device_id": self.device.device_id,
            "heart_rate": 92,
            "spo2": 98,
            "temperature": 37.1,
        })

        self.assertEqual(response.status_code, 409)
        self.assertEqual(IoTVital.objects.count(), 0)
        self.assertEqual(TelemetryLog.objects.count(), 0)


class QueueWorkflowTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = get_user_model().objects.create_user(
            username="nurse",
            password="secret",
        )
        self.client.force_login(self.user)

    def register_patient(self):
        return self.client.post(reverse("register_patient"), {
            "first_name": "Demo",
            "last_name": "Queue",
            "national_id": "1234567890999",
            "gender": "M",
            "age": "31",
            "phone": "0812345678",
            "blood_type": "UNKNOWN",
            "bp_sys": "118",
            "bp_dia": "76",
            "note": "เวียนหัวเล็กน้อย",
        })

    def test_qr_registration_starts_waiting_vitals_without_default_green(self):
        response = self.register_patient()

        self.assertRedirects(response, reverse("waiting_vitals"))
        visit = Visit.objects.select_related("queue").get()
        self.assertEqual(visit.queue.status, Queue.Status.WAITING_VITALS)
        self.assertIsNone(visit.final_severity)
        self.assertFalse(hasattr(visit, "triage_result"))

        vitals = VitalSign.objects.get(visit=visit)
        self.assertEqual(vitals.sys_bp, 118)
        self.assertEqual(vitals.dia_bp, 76)
        self.assertIsNone(vitals.rr)

    def test_waiting_vitals_shows_patient_detail_modal(self):
        self.register_patient()
        patient = Patient.objects.get()

        response = self.client.get(reverse("waiting_vitals"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "ดูข้อมูลผู้ป่วย")
        self.assertContains(response, f'id="patient-modal-{patient.visits.get().queue.id}"')
        self.assertContains(response, patient.phone)

    def test_manual_vitals_then_ai_then_nurse_confirmation_enters_prioritized_queue(self):
        self.register_patient()
        visit = Visit.objects.select_related("queue").get()

        response = self.client.post(reverse("nurse_triage_assessment", args=[visit.id]), {
            "action": "evaluate",
            "rr": "18",
            "pr": "84",
            "sys_bp": "118",
            "dia_bp": "76",
            "bt": "37.0",
            "o2sat": "98",
            "pain_score": "2",
            "symptoms": "เวียนหัวเล็กน้อย",
        })
        self.assertRedirects(response, reverse("waiting_confirmation"))

        visit.refresh_from_db()
        visit.queue.refresh_from_db()
        self.assertEqual(visit.queue.status, Queue.Status.WAITING_CONFIRMATION)
        self.assertIsNone(visit.final_severity)
        self.assertEqual(visit.triage_result.ai_severity, "GREEN")
        self.assertIsNone(visit.triage_result.nurse_severity)

        response = self.client.post(reverse("triage_visit", args=[visit.id]), {
            "severity": "YELLOW",
            "nurse_note": "ปรับตามอาการหน้าห้อง",
        })
        self.assertRedirects(response, reverse("queue_list"))

        visit.refresh_from_db()
        visit.queue.refresh_from_db()
        triage = visit.triage_result
        self.assertEqual(triage.nurse_severity, "YELLOW")
        self.assertEqual(visit.final_severity, "YELLOW")
        self.assertIsNotNone(visit.confirmed_at)
        self.assertEqual(visit.queue.status, Queue.Status.WAITING_QUEUE)
        self.assertEqual(visit.queue.priority, 2)

        response = self.client.get(reverse("queue_list"))
        self.assertContains(response, "Demo Queue")
        self.assertContains(response, "YELLOW")

    def test_waiting_confirmation_can_return_to_waiting_vitals(self):
        self.register_patient()
        visit = Visit.objects.select_related("queue").get()

        response = self.client.post(reverse("nurse_triage_assessment", args=[visit.id]), {
            "action": "evaluate",
            "rr": "18",
            "pr": "84",
            "sys_bp": "118",
            "dia_bp": "76",
            "bt": "37.0",
            "o2sat": "98",
            "pain_score": "2",
            "symptoms": "เวียนหัวเล็กน้อย",
        })
        self.assertRedirects(response, reverse("waiting_confirmation"))

        response = self.client.post(reverse("return_to_waiting_vitals", args=[visit.id]))
        self.assertRedirects(response, reverse("waiting_vitals"))

        visit.queue.refresh_from_db()
        self.assertEqual(visit.queue.status, Queue.Status.WAITING_VITALS)
