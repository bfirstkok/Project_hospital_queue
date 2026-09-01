import csv
import json
import tempfile
from datetime import timedelta
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import Client, TestCase, override_settings
from django.urls import resolve, reverse
from django.utils import timezone

from patients.models import Patient
from queues import views as queue_views
from queues.forms import DeviceManagementPairForm, DevicePairingForm
from queues.models import CriticalAlert, Device, DeviceAssignment, IoTVital, NurseCareAssignment, Queue, StaffDuty, StaffProfile, TelemetryLog, TriageResult, Visit, VitalSign


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
        self.visit = Visit.objects.create(patient=self.patient, final_severity=Visit.Severity.YELLOW)
        Queue.objects.create(visit=self.visit, status=Queue.Status.OBSERVATION_MONITORING, priority=2)
        self.other_visit = Visit.objects.create(patient=self.patient, final_severity=Visit.Severity.YELLOW)
        Queue.objects.create(visit=self.other_visit, status=Queue.Status.OBSERVATION_MONITORING, priority=2)
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
        self.assertIsNone(log.sys_bp)
        self.assertIsNone(log.dia_bp)

        vitals = VitalSign.objects.get(visit=self.visit)
        self.assertEqual(vitals.pr, 92)
        self.assertEqual(vitals.o2sat, 98)
        self.assertIsNone(vitals.sys_bp)
        self.assertIsNone(vitals.dia_bp)

    def test_mismatched_visit_id_is_rejected(self):
        DeviceAssignment.objects.create(device=self.device, visit=self.visit)

        response = self.post_telemetry({
            "visit_id": self.other_visit.id,
            "vitals": {"bpm": 101},
        })

        self.assertEqual(response.status_code, 409)
        self.assertEqual(TelemetryLog.objects.count(), 0)

    def test_wearable_rejects_visit_before_nurse_confirms_yellow(self):
        waiting_visit = Visit.objects.create(patient=self.patient, final_severity=None)
        Queue.objects.create(visit=waiting_visit, status=Queue.Status.WAITING_VITALS)
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

        self.assertEqual(response.status_code, 409)
        self.assertEqual(TelemetryLog.objects.count(), 0)

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
        self.assertIsNone(vital.blood_pressure_sys)
        self.assertIsNone(vital.blood_pressure_dia)

        log = TelemetryLog.objects.get()
        self.assertEqual(log.visit, self.visit)
        self.assertEqual(log.device, self.device)
        self.assertIsNone(log.sys_bp)
        self.assertIsNone(log.dia_bp)

        vitals = VitalSign.objects.get(visit=self.visit)
        self.assertIsNone(vitals.sys_bp)
        self.assertIsNone(vitals.dia_bp)

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

    def test_post_opd_monitoring_patient_can_send_wearable_telemetry(self):
        self.visit.final_severity = Visit.Severity.GREEN
        self.visit.save(update_fields=["final_severity"])
        self.visit.queue.status = Queue.Status.MONITORING
        self.visit.queue.save(update_fields=["status"])
        DeviceAssignment.objects.create(device=self.device, visit=self.visit)

        response = self.post_telemetry({
            "vitals": {"bpm": 84, "o2sat": 98, "bt": 36.8, "rr": 18},
        })

        self.assertEqual(response.status_code, 200)
        self.assertTrue(TelemetryLog.objects.filter(visit=self.visit, device=self.device).exists())


class ObservationMonitoringVisibilityTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="monitor-nurse",
            password="secret",
        )
        self.client.force_login(self.user)
        patient = Patient.objects.create(
            first_name="Observation",
            last_name="Patient",
            national_id="1234500000001",
        )
        self.visit = Visit.objects.create(
            patient=patient,
            final_severity=Visit.Severity.YELLOW,
        )
        Queue.objects.create(
            visit=self.visit,
            status=Queue.Status.OBSERVATION_MONITORING,
            priority=2,
        )
        device = Device.objects.create(
            device_id="OBS-001",
            api_key="secret",
            is_active=True,
        )
        DeviceAssignment.objects.create(device=device, visit=self.visit)

    def test_main_monitor_route_uses_waiting_area_monitor(self):
        match = resolve(reverse("monitor_dashboard"))

        self.assertIs(match.func, queue_views.monitor_dashboard)

    def test_monitor_summary_includes_paired_observation_visit(self):
        response = self.client.get(reverse("monitor_summary_api"))

        self.assertEqual(response.status_code, 200)
        visit_ids = [item["visit_id"] for item in response.json()["items"]]
        self.assertIn(str(self.visit.id), visit_ids)

    def test_monitor_visit_id_is_serialized_without_javascript_rounding(self):
        response = self.client.get(reverse("monitor_summary_api"))

        summary_item = response.json()["items"][0]
        visit_id = summary_item["visit_id"]
        self.assertIsInstance(visit_id, str)
        self.assertEqual(visit_id, str(self.visit.id))
        self.assertNotIn("sys_bp", summary_item["vitals"])
        self.assertNotIn("dia_bp", summary_item["vitals"])

        latest_response = self.client.get(reverse("monitor_latest_api"))
        latest_row = latest_response.json()["rows"][0]
        latest_visit_id = latest_row["visit_id"]
        self.assertIsInstance(latest_visit_id, str)
        self.assertEqual(latest_visit_id, str(self.visit.id))
        self.assertNotIn("sys_bp", latest_row)
        self.assertNotIn("dia_bp", latest_row)


class PersonnelDashboardTests(TestCase):
    def setUp(self):
        self.manager = get_user_model().objects.create_user(
            username="head-nurse",
            password="secret",
            first_name="หัวหน้า",
            last_name="พยาบาล",
        )
        self.nurse = get_user_model().objects.create_user(
            username="nurse-a",
            password="secret",
            first_name="พยาบาล",
            last_name="เอ",
        )
        StaffProfile.objects.create(user=self.manager, role=StaffProfile.Role.NURSE)
        StaffProfile.objects.create(user=self.nurse, role=StaffProfile.Role.NURSE)
        self.client.force_login(self.manager)
        self.patient = Patient.objects.create(
            first_name="Wearable",
            last_name="Patient",
            national_id="1234500000099",
        )
        self.visit = Visit.objects.create(
            patient=self.patient,
            final_severity=Visit.Severity.YELLOW,
        )
        Queue.objects.create(
            visit=self.visit,
            status=Queue.Status.OBSERVATION_MONITORING,
            priority=3,
        )
        self.device = Device.objects.create(
            device_id="STAFF-WATCH-1",
            api_key="secret",
            is_active=True,
        )
        DeviceAssignment.objects.create(device=self.device, visit=self.visit)
        self.duty = StaffDuty.objects.create(
            user=self.nurse,
            duty_date=timezone.localdate(),
            is_present=True,
            is_available=True,
            last_seen_at=timezone.now(),
        )

    def test_personnel_page_lists_online_staff_and_wearable_patient(self):
        response = self.client.get(reverse("personnel_dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "บุคลากรและผู้ป่วยที่รับผิดชอบ")
        self.assertContains(response, "พยาบาล เอ")
        self.assertContains(response, "Wearable Patient")
        self.assertContains(response, self.device.device_id)
        self.assertFalse(StaffDuty.objects.filter(
            user=self.manager,
            duty_date=timezone.localdate(),
        ).exists())

    def test_online_nurse_can_be_assigned_to_eligible_patient(self):
        response = self.client.post(reverse("personnel_dashboard"), {
            "action": "assign_patient",
            "nurse_id": self.nurse.id,
            "visit_id": self.visit.id,
        })

        self.assertRedirects(response, reverse("personnel_dashboard"))
        assignment = NurseCareAssignment.objects.get(visit=self.visit, is_active=True)
        self.assertEqual(assignment.nurse, self.nurse)
        self.assertEqual(assignment.assigned_by, self.manager)

    def test_heartbeat_refreshes_signed_in_staff_activity(self):
        manager_duty = StaffDuty.objects.create(
            user=self.manager,
            duty_date=timezone.localdate(),
            is_present=True,
            is_available=True,
            last_seen_at=timezone.now() - timedelta(minutes=10),
        )

        response = self.client.get(reverse("staff_heartbeat"))

        self.assertEqual(response.status_code, 200)
        manager_duty.refresh_from_db()
        self.assertGreater(manager_duty.last_seen_at, timezone.now() - timedelta(minutes=1))

    def test_unavailable_nurse_cannot_receive_new_assignment(self):
        self.duty.is_available = False
        self.duty.save(update_fields=["is_available"])

        self.client.post(reverse("personnel_dashboard"), {
            "action": "assign_patient",
            "nurse_id": self.nurse.id,
            "visit_id": self.visit.id,
        })

        self.assertFalse(NurseCareAssignment.objects.filter(visit=self.visit, is_active=True).exists())

    def test_non_nurse_cannot_receive_new_assignment(self):
        self.nurse.hospital_staff_profile.role = StaffProfile.Role.DOCTOR
        self.nurse.hospital_staff_profile.save(update_fields=["role"])

        self.client.post(reverse("personnel_dashboard"), {
            "action": "assign_patient",
            "nurse_id": self.nurse.id,
            "visit_id": self.visit.id,
        })

        self.assertFalse(NurseCareAssignment.objects.filter(visit=self.visit, is_active=True).exists())

    def test_manager_can_check_in_and_make_nurse_available_without_nurse_login(self):
        other_nurse = get_user_model().objects.create_user(username="nurse-b", password="secret")
        StaffProfile.objects.create(user=other_nurse, role=StaffProfile.Role.NURSE)

        self.client.post(reverse("personnel_dashboard"), {
            "action": "set_attendance",
            "user_id": other_nurse.id,
            "is_present": "1",
        })
        self.client.post(reverse("personnel_dashboard"), {
            "action": "set_availability",
            "user_id": other_nurse.id,
            "is_available": "1",
        })

        duty = StaffDuty.objects.get(user=other_nurse, duty_date=timezone.localdate())
        self.assertTrue(duty.is_present)
        self.assertTrue(duty.is_available)

    def test_manager_can_set_real_name_and_private_staff_photo(self):
        with tempfile.TemporaryDirectory() as media_root, override_settings(MEDIA_ROOT=media_root):
            photo = SimpleUploadedFile(
                "nurse.png",
                b"\x89PNG\r\n\x1a\nprofile-photo",
                content_type="image/png",
            )
            response = self.client.post(reverse("personnel_dashboard"), {
                "action": "update_staff_identity",
                "user_id": self.nurse.id,
                "first_name": "วิภา",
                "last_name": "ใจดี",
                "photo": photo,
            })

            self.assertRedirects(response, reverse("personnel_dashboard"))
            self.nurse.refresh_from_db()
            self.nurse.hospital_staff_profile.refresh_from_db()
            self.assertEqual(self.nurse.get_full_name(), "วิภา ใจดี")
            self.assertTrue(self.nurse.hospital_staff_profile.photo.name)

            photo_response = self.client.get(reverse(
                "staff_photo",
                args=[self.nurse.hospital_staff_profile.id],
            ))
            self.assertEqual(photo_response.status_code, 200)
            self.assertEqual(photo_response["Content-Type"], "image/png")
            photo_response.close()

    def test_patient_without_monitoring_status_is_not_assignable(self):
        self.visit.queue.status = Queue.Status.WAITING_QUEUE
        self.visit.queue.save(update_fields=["status"])

        response = self.client.post(reverse("personnel_dashboard"), {
            "action": "assign_patient",
            "nurse_id": self.nurse.id,
            "visit_id": self.visit.id,
        })

        self.assertRedirects(response, reverse("personnel_dashboard"))
        self.assertFalse(NurseCareAssignment.objects.filter(visit=self.visit, is_active=True).exists())

    def test_post_opd_monitoring_visit_can_pair_without_changing_status(self):
        inpatient = Visit.objects.create(
            patient=self.patient,
            final_severity=Visit.Severity.GREEN,
        )
        Queue.objects.create(visit=inpatient, status=Queue.Status.MONITORING, priority=4)
        inpatient_device = Device.objects.create(
            device_id="STAFF-WATCH-2",
            api_key="secret-2",
            is_active=True,
        )

        response = self.client.post(reverse("device_management"), {
            "action": "pair_device",
            "device": inpatient_device.id,
            "visit": inpatient.id,
        })

        self.assertRedirects(response, reverse("device_management"))
        inpatient.queue.refresh_from_db()
        self.assertEqual(inpatient.queue.status, Queue.Status.MONITORING)
        self.assertTrue(DeviceAssignment.objects.filter(visit=inpatient, device=inpatient_device, is_active=True).exists())

    def test_unpairing_wearable_ends_active_nurse_assignment(self):
        care_assignment = NurseCareAssignment.objects.create(
            nurse=self.nurse,
            visit=self.visit,
            assigned_by=self.manager,
        )
        device_assignment = DeviceAssignment.objects.get(visit=self.visit, is_active=True)

        self.client.post(reverse("device_management"), {
            "action": "unpair_device",
            "assignment_id": device_assignment.id,
        })

        care_assignment.refresh_from_db()
        self.assertFalse(care_assignment.is_active)
        self.assertIsNotNone(care_assignment.ended_at)


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
        self.assertContains(response, 'class="waiting-vitals-page"')
        self.assertContains(response, "padding-top: 62px")
        self.assertContains(response, "ดูข้อมูลผู้ป่วย")
        self.assertContains(response, "แก้ไขข้อมูลผู้ป่วย")
        self.assertContains(response, reverse("edit_patient", args=[patient.id]))
        self.assertContains(response, "ประเมินสุขภาพ")
        self.assertNotContains(response, "กรอกค่าด้วยตนเอง")
        self.assertContains(response, f'id="patient-modal-{patient.visits.get().queue.id}"')
        self.assertContains(response, patient.phone)
        self.assertContains(response, "ยังไม่มีข้อมูลวันเดือนปีเกิด")
        self.assertContains(response, reverse("update_patient_birth_date", args=[patient.id]))

    def test_health_assessment_has_clear_three_step_form(self):
        self.register_patient()
        visit = Visit.objects.get()

        response = self.client.get(reverse("nurse_triage_assessment", args=[visit.id]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "ประเมินสุขภาพผู้ป่วย")
        self.assertContains(response, "กรอกข้อมูลตามลำดับ")
        self.assertContains(response, "อาการและปัจจัยเสี่ยง")
        self.assertContains(response, "จุดตัดสินใจสำหรับการคัดกรอง 5 ระดับ")
        self.assertContains(response, "คาดว่าจะต้องตรวจหรือรักษาเพิ่มเติมกี่ประเภท")
        self.assertContains(response, "0 ประเภท → สีขาว")
        self.assertContains(response, "1 ประเภท → สีเขียว")
        self.assertContains(response, "2 ประเภทขึ้นไป → สีเหลือง")
        self.assertContains(response, "ตรวจเลือดหลายรายการยังนับเป็น 1 ประเภท")
        self.assertContains(response, "ประเมินและส่งไปรอยืนยัน")
        self.assertContains(response, "ไม่พบเกณฑ์เตือนอัตโนมัติจากค่านี้")
        self.assertContains(response, "วิกฤต: SpO₂ < 90%", html=False)
        self.assertContains(response, "เฝ้าระวัง: RR 21–30 ครั้ง/นาที")
        self.assertContains(response, "ยังไม่ได้วัด — ระบบจะไม่ใช้ค่านี้ในการประเมิน")
        self.assertNotContains(response, "AI Suggested Severity")
        self.assertNotContains(response, "Rule Guardrail Status")

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
            "lifesaving_intervention": "no",
            "high_risk_condition": "no",
            "mental_status": "ALERT",
            "severe_distress": "no",
            "expected_resources": "1",
        })
        self.assertRedirects(response, reverse("waiting_confirmation"))

        visit.refresh_from_db()
        visit.queue.refresh_from_db()
        self.assertEqual(visit.queue.status, Queue.Status.WAITING_CONFIRMATION)
        self.assertIsNone(visit.final_severity)
        self.assertEqual(visit.triage_result.ai_severity, "GREEN")
        self.assertIsNone(visit.triage_result.nurse_severity)
        self.assertFalse(visit.triage_result.lifesaving_intervention)
        self.assertEqual(visit.triage_result.expected_resources, "1")

        confirmation_page = self.client.get(reverse("waiting_confirmation"))
        self.assertEqual(confirmation_page.status_code, 200)
        self.assertContains(confirmation_page, "พยาบาลยืนยันผลคัดกรอง")
        self.assertContains(confirmation_page, "คำแนะนำจากระบบ")
        self.assertContains(confirmation_page, "พยาบาลตรวจและยืนยัน")
        self.assertContains(confirmation_page, "ยืนยันตามคำแนะนำ · สีเขียว")
        self.assertContains(confirmation_page, "กลับไปประเมินข้อมูลสุขภาพใหม่")
        self.assertContains(confirmation_page, "background: #dc2626 !important")
        self.assertContains(confirmation_page, "background: #db2777 !important")
        self.assertContains(confirmation_page, "background: #facc15 !important")
        self.assertContains(confirmation_page, "background: #16a34a !important")
        self.assertContains(confirmation_page, "confirmation-solid-20260821")
        self.assertNotContains(confirmation_page, "AI DECISION SUPPORT")
        self.assertNotContains(confirmation_page, "FINAL DECISION")

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
        self.assertEqual(visit.queue.priority, 3)

        response = self.client.get(reverse("queue_list"))
        self.assertContains(response, "Demo Queue")
        self.assertContains(response, "YELLOW")
        self.assertContains(response, "เวลารอ")
        self.assertContains(response, "js-wait-time")
        self.assertContains(response, "formatWaitDuration")
        self.assertContains(response, "data-end=\"\"")
        self.assertContains(response, "สีเหลือง · เร่งด่วน")
        self.assertContains(response, "เปลี่ยนระดับ…")
        self.assertContains(response, "เปิดจอแสดงคิวผู้ป่วย")
        self.assertContains(response, reverse("queue_display"))

        # The waiting-room screen is public but must never expose patient data.
        self.client.logout()
        display = self.client.get(reverse("queue_display"))
        self.assertEqual(display.status_code, 200)
        self.assertContains(display, visit.queue.display_number)
        self.assertContains(display, "ข้อมูลรีเฟรชอัตโนมัติทุก 30 วินาที")
        self.assertContains(display, "formatWaitDuration")
        self.assertContains(display, "เร่งด่วน")
        self.assertNotContains(display, "Demo Queue")
        self.assertNotContains(display, visit.patient.national_id)

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
            "lifesaving_intervention": "no",
            "high_risk_condition": "no",
            "mental_status": "ALERT",
            "severe_distress": "no",
            "expected_resources": "1",
        })
        self.assertRedirects(response, reverse("waiting_confirmation"))

        response = self.client.post(reverse("return_to_waiting_vitals", args=[visit.id]))
        self.assertRedirects(response, reverse("waiting_vitals"))

        visit.queue.refresh_from_db()
        self.assertEqual(visit.queue.status, Queue.Status.WAITING_VITALS)

class ConfirmedTriageFlowTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = get_user_model().objects.create_user(username="flow-nurse", password="secret")
        self.client.force_login(self.user)
        self.patient = Patient.objects.create(
            first_name="Flow",
            last_name="Patient",
            national_id="1234567890888",
        )
        self.device = Device.objects.create(device_id="WATCH-FLOW", api_key="flow-secret", is_active=True)

    def make_visit(self, status=Queue.Status.WAITING_CONFIRMATION, severity=None):
        visit = Visit.objects.create(patient=self.patient, final_severity=severity)
        Queue.objects.create(visit=visit, status=status)
        TriageResult.objects.create(visit=visit, ai_severity=Visit.Severity.YELLOW)
        return visit

    def confirm(self, visit, severity):
        return self.client.post(
            reverse("triage_visit", args=[visit.id]),
            {"severity": severity, "nurse_note": "ยืนยันโดยพยาบาล"},
        )

    def test_red_bypasses_opd_queue_and_unpairs_wearable(self):
        visit = self.make_visit()
        assignment = DeviceAssignment.objects.create(device=self.device, visit=visit)

        response = self.confirm(visit, Visit.Severity.RED)

        self.assertRedirects(response, reverse("emergency_transfers"))
        visit.refresh_from_db()
        visit.queue.refresh_from_db()
        assignment.refresh_from_db()
        self.assertEqual(visit.queue.status, Queue.Status.EMERGENCY_TRANSFER)
        self.assertEqual(visit.queue.priority, 1)
        self.assertFalse(assignment.is_active)
        self.assertIsNotNone(assignment.unpaired_at)

        page = self.client.get(reverse("emergency_transfers"))
        self.assertContains(page, 'class="topbar"')
        self.assertContains(page, "ผู้ป่วยระดับ 1–2 ที่ต้องรับช่วงในระบบฉุกเฉิน")
        self.assertContains(page, "backdrop-filter: none")

    def test_yellow_enters_observation_queue_and_can_pair_wearable(self):
        visit = self.make_visit()
        self.confirm(visit, Visit.Severity.YELLOW)
        visit.queue.refresh_from_db()
        self.assertEqual(visit.queue.status, Queue.Status.WAITING_QUEUE)

        response = self.client.post(reverse("device_management"), {
            "action": "pair_device",
            "device": self.device.id,
            "visit": visit.id,
        })

        self.assertRedirects(response, reverse("device_management"))
        visit.queue.refresh_from_db()
        self.assertEqual(visit.queue.status, Queue.Status.OBSERVATION_MONITORING)
        self.assertTrue(DeviceAssignment.objects.filter(visit=visit, device=self.device, is_active=True).exists())

    def test_pink_bypasses_opd_queue(self):
        visit = self.make_visit()

        response = self.confirm(visit, Visit.Severity.PINK)

        self.assertRedirects(response, reverse("emergency_transfers"))
        visit.queue.refresh_from_db()
        self.assertEqual(visit.queue.status, Queue.Status.EMERGENCY_TRANSFER)
        self.assertEqual(visit.queue.priority, 2)

    def test_white_enters_normal_queue_and_cannot_pair_wearable(self):
        visit = self.make_visit()
        self.confirm(visit, Visit.Severity.WHITE)
        visit.queue.refresh_from_db()
        self.assertEqual(visit.queue.status, Queue.Status.WAITING_QUEUE)
        self.assertEqual(visit.queue.priority, 5)
        self.assertNotIn(visit, DevicePairingForm().fields["visit"].queryset)

    def test_green_enters_normal_queue_and_cannot_pair_wearable(self):
        visit = self.make_visit()
        self.confirm(visit, Visit.Severity.GREEN)
        visit.queue.refresh_from_db()
        self.assertEqual(visit.queue.status, Queue.Status.WAITING_QUEUE)

        self.assertNotIn(visit, DevicePairingForm().fields["visit"].queryset)
        self.assertNotIn(visit, DeviceManagementPairForm().fields["visit"].queryset)

    def test_nurse_override_requires_additional_reason(self):
        visit = self.make_visit()

        response = self.client.post(
            reverse("triage_visit", args=[visit.id]),
            {"severity": Visit.Severity.RED, "nurse_note": ""},
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("waiting_confirmation"))
        visit.refresh_from_db()
        visit.queue.refresh_from_db()
        self.assertIsNone(visit.final_severity)
        self.assertEqual(visit.queue.status, Queue.Status.WAITING_CONFIRMATION)
        self.assertIsNone(visit.triage_result.nurse_severity)

        page = self.client.get(reverse("waiting_confirmation"))
        self.assertContains(page, "กรุณาระบุเหตุผลเพิ่มเติมเมื่อยืนยันระดับต่างจากคำแนะนำของ AI")
        self.assertContains(page, "data-ai-severity=\"YELLOW\"")
        self.assertContains(page, "การเปลี่ยนจาก${severityLabels[aiSeverity]}เป็น${severityLabels[selectedSeverity]}")
        self.assertContains(page, 'class="criteria-sidebar"')
        self.assertContains(page, "เกณฑ์ระดับคัดกรอง")
        self.assertContains(page, "สีแดง · ช่วยชีวิตทันที")
        self.assertContains(page, "สีชมพู · ฉุกเฉิน")
        self.assertContains(page, "สีเหลือง · เฝ้าระวัง")
        self.assertContains(page, "สีเขียว · ไม่เร่งด่วน")
        self.assertContains(page, "สีขาว · ผู้ป่วยทั่วไป")

    def test_nurse_can_override_red_to_yellow_with_reason(self):
        visit = self.make_visit()
        visit.triage_result.ai_severity = Visit.Severity.RED
        visit.triage_result.save(update_fields=["ai_severity"])

        response = self.client.post(
            reverse("triage_visit", args=[visit.id]),
            {"severity": Visit.Severity.YELLOW, "nurse_note": "ประเมินซ้ำแล้วรู้สึกตัวดีและสัญญาณชีพคงที่"},
        )

        self.assertRedirects(response, reverse("queue_list"))
        visit.refresh_from_db()
        visit.queue.refresh_from_db()
        self.assertEqual(visit.final_severity, Visit.Severity.YELLOW)
        self.assertEqual(visit.triage_result.nurse_severity, Visit.Severity.YELLOW)
        self.assertEqual(visit.queue.status, Queue.Status.WAITING_QUEUE)

    def test_nurse_confirmation_updates_special_groups(self):
        visit = self.make_visit()

        response = self.client.post(
            reverse("triage_visit", args=[visit.id]),
            {
                "severity": Visit.Severity.YELLOW,
                "nurse_note": "",
                "risk_flags_present": "1",
                "risk_flags": ["elderly_80", "pregnant"],
            },
        )

        self.assertRedirects(response, reverse("queue_list"))
        self.assertEqual(
            visit.vitals.risk_flags,
            ["elderly_80", "pregnant"],
        )

    def test_abnormal_yellow_wearable_data_requires_nurse_reassessment(self):
        visit = self.make_visit(status=Queue.Status.OBSERVATION_MONITORING, severity=Visit.Severity.YELLOW)
        DeviceAssignment.objects.create(device=self.device, visit=visit)

        response = self.client.post(
            reverse("iot_telemetry"),
            data=json.dumps({
                "visit_id": visit.id,
                "vitals": {
                    "bpm": 100,
                    "o2sat": 90,
                    "bt": 37.2,
                    "rr": 20,
                    "sys_bp": 120,
                    "dia_bp": 80,
                },
            }),
            content_type="application/json",
            HTTP_X_DEVICE_ID=self.device.device_id,
            HTTP_X_API_KEY=self.device.api_key,
        )

        self.assertEqual(response.status_code, 200)
        visit.queue.refresh_from_db()
        self.assertEqual(visit.queue.status, Queue.Status.REASSESSMENT_REQUIRED)
        self.assertTrue(CriticalAlert.objects.filter(visit=visit, status=CriticalAlert.Status.NEW).exists())
        self.assertEqual(visit.final_severity, Visit.Severity.YELLOW)

    def test_reassessment_can_return_yellow_patient_to_monitoring(self):
        visit = self.make_visit(status=Queue.Status.REASSESSMENT_REQUIRED, severity=Visit.Severity.YELLOW)
        DeviceAssignment.objects.create(device=self.device, visit=visit)

        self.confirm(visit, Visit.Severity.YELLOW)

        visit.queue.refresh_from_db()
        self.assertEqual(visit.queue.status, Queue.Status.OBSERVATION_MONITORING)


class ConfirmedTriageExportTests(TestCase):
    def test_export_contains_complete_deidentified_training_row(self):
        patient = Patient.objects.create(
            first_name="Private",
            last_name="Patient",
            national_id="9876543210123",
            age=45,
        )
        visit = Visit.objects.create(patient=patient, note="เวียนหัว")
        VitalSign.objects.create(
            visit=visit,
            rr=18,
            pr=82,
            sys_bp=120,
            dia_bp=80,
            bt=36.8,
            o2sat=98,
            pain_score=2,
        )
        TriageResult.objects.create(
            visit=visit,
            nurse_severity=Visit.Severity.GREEN,
            lifesaving_intervention=False,
            high_risk_condition=False,
            altered_mental_status=False,
            mental_status="ALERT",
            severe_distress=False,
            expected_resources="1",
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "confirmed.csv"
            call_command("export_confirmed_triage", output=str(output), verbosity=0)
            with output.open(encoding="utf-8-sig", newline="") as handle:
                rows = list(csv.DictReader(handle))

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["label"], "GREEN")
        self.assertEqual(rows[0]["expected_resources"], "1")
        self.assertNotIn("national_id", rows[0])
        self.assertNotIn("first_name", rows[0])
        self.assertNotIn("phone", rows[0])

