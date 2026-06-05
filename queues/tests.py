import json

from django.test import Client, TestCase

from patients.models import Patient
from queues.models import Device, DeviceAssignment, Queue, TelemetryLog, Visit, VitalSign


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
