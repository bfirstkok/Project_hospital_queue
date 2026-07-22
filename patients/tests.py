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

    def post_login(self, national_id=None):
        return self.client.post(
            "/api/patient/login/",
            data=json.dumps({"national_id": national_id or self.payload["national_id"]}),
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
        self.assertTrue(response.json()["access_token"])

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


    def test_login_me_and_queue_with_bearer_token(self):
        self.post_registration()
        login_response = self.post_login()

        self.assertEqual(login_response.status_code, 200)
        self.assertEqual(
            login_response["Access-Control-Allow-Headers"],
            "Content-Type, Authorization",
        )
        token = login_response.json()["access_token"]
        headers = {
            "HTTP_AUTHORIZATION": f"Bearer {token}",
            "HTTP_ORIGIN": "https://bfirstkok.github.io",
        }

        me_response = self.client.get("/api/patient/me/", **headers)
        queue_response = self.client.get("/api/patient/queue/", **headers)

        self.assertEqual(me_response.status_code, 200)
        self.assertEqual(queue_response.status_code, 200)
        self.assertEqual(me_response.json()["profile"]["hn"], Patient.objects.get().hn)
        self.assertEqual(me_response.json()["profile"]["national_id"], "1-xxxx-xxxxx-xx-3")
        self.assertEqual(queue_response.json()["status"], Queue.Status.WAITING_VITALS)

    def test_protected_endpoints_reject_missing_or_tampered_token(self):
        self.post_registration()
        token = self.post_login().json()["access_token"]

        missing = self.client.get("/api/patient/me/")
        tampered = self.client.get(
            "/api/patient/queue/",
            HTTP_AUTHORIZATION=f"Bearer {token}tampered",
        )

        self.assertEqual(missing.status_code, 401)
        self.assertEqual(tampered.status_code, 401)

    def test_login_rejects_unknown_patient(self):
        response = self.post_login("9999999999999")

        self.assertEqual(response.status_code, 401)
        self.assertFalse(response.json()["ok"])
