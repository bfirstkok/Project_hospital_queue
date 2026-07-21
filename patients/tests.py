import json
import uuid

from django.core.cache import cache
from django.test import TestCase, override_settings

from queues.models import Queue, Visit, VitalSign
from .models import Patient


@override_settings(PATIENT_APP_ORIGINS={"https://bfirstkok.github.io"})
class PublicPatientApiTests(TestCase):
    endpoint = "/api/patient/register/"

    def setUp(self):
        cache.clear()
        self.payload = {
            "first_name": "สมชาย",
            "last_name": "ใจดี",
            "national_id": "1234567890123",
            "gender": "M",
            "age": 31,
            "phone": "0812345678",
            "blood_type": "UNKNOWN",
            "note": "เวียนหัว",
            "consent": True,
        }

    def post_registration(self, payload=None):
        return self.client.post(
            self.endpoint,
            data=json.dumps(payload or self.payload),
            content_type="application/json",
            HTTP_ORIGIN="https://bfirstkok.github.io",
        )

    def test_registration_creates_waiting_vitals_visit_without_vital_values(self):
        response = self.post_registration()

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response["Access-Control-Allow-Origin"], "https://bfirstkok.github.io")
        visit = Visit.objects.select_related("queue", "vitals").get()
        self.assertEqual(visit.queue.status, Queue.Status.WAITING_VITALS)
        self.assertIsNone(visit.vitals.sys_bp)
        self.assertIsNone(visit.vitals.dia_bp)
        self.assertEqual(response.json()["tracking_token"], str(visit.tracking_token))

    def test_duplicate_submit_reuses_active_visit(self):
        first = self.post_registration()
        second = self.post_registration()

        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(Visit.objects.count(), 1)
        self.assertEqual(first.json()["tracking_token"], second.json()["tracking_token"])

    def test_invalid_registration_returns_field_errors(self):
        self.payload["national_id"] = "123"
        response = self.post_registration()

        self.assertEqual(response.status_code, 400)
        self.assertIn("national_id", response.json()["errors"])
        self.assertEqual(Patient.objects.count(), 0)

    def test_queue_status_does_not_expose_patient_or_severity(self):
        self.post_registration()
        visit = Visit.objects.select_related("queue").get()
        response = self.client.get(
            f"/api/patient/queue/{visit.tracking_token}/",
            HTTP_ORIGIN="https://bfirstkok.github.io",
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["status"], Queue.Status.WAITING_VITALS)
        self.assertNotIn("patient", payload)
        self.assertNotIn("severity", payload)

    def test_unknown_tracking_token_returns_json_404(self):
        response = self.client.get(f"/api/patient/queue/{uuid.uuid4()}/")

        self.assertEqual(response.status_code, 404)
        self.assertFalse(response.json()["ok"])
