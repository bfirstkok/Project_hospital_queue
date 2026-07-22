import json
from datetime import timedelta

from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from queues.models import Queue, Visit, VitalSign

from .models import Patient, PatientAccessToken


@override_settings(
    PATIENT_APP_ORIGINS={"https://patient.example.com"},
    PATIENT_TOKEN_MAX_AGE=3600,
)
class PatientPortalApiTests(TestCase):
    def setUp(self):
        cache.clear()
        self.patient = Patient.objects.create(
            first_name="สมชาย",
            last_name="ใจดี",
            national_id="1234567890123",
            phone="0812345678",
        )
        self.visit = Visit.objects.create(patient=self.patient, note="เวียนศีรษะ")
        VitalSign.objects.create(visit=self.visit, pr=80, o2sat=99)
        self.queue = Queue.objects.create(
            visit=self.visit,
            status=Queue.Status.WAITING_QUEUE,
            exam_room=2,
        )

    def post_json(self, url, payload):
        return self.client.post(url, data=json.dumps(payload), content_type="application/json")

    def login(self, national_id=None):
        response = self.post_json(
            reverse("public_patient_login"),
            {"national_id": national_id or self.patient.national_id},
        )
        return response

    @staticmethod
    def bearer(token):
        return {"HTTP_AUTHORIZATION": f"Bearer {token}"}

    def test_login_success_returns_opaque_token_without_patient_data(self):
        response = self.login()

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["access_token"])
        self.assertEqual(payload["token_type"], "Bearer")
        self.assertNotIn(self.patient.national_id, response.content.decode())
        stored = PatientAccessToken.objects.get(patient=self.patient)
        self.assertNotEqual(stored.token_hash, payload["access_token"])

    def test_login_rejects_unknown_patient(self):
        response = self.login("9999999999999")

        self.assertEqual(response.status_code, 401)
        self.assertFalse(response.json()["ok"])

    def test_login_rate_limit_blocks_request_after_ten_attempts(self):
        login_url = reverse("public_patient_login")
        for _ in range(10):
            response = self.post_json(login_url, {})
            self.assertEqual(response.status_code, 400)

        blocked = self.post_json(login_url, {})
        self.assertEqual(blocked.status_code, 429)

    def test_security_audit_does_not_log_patient_id_or_access_token(self):
        with self.assertLogs("security.audit", level="INFO") as captured:
            response = self.login()

        self.assertEqual(response.status_code, 200)
        output = "\n".join(captured.output)
        self.assertIn('"route":"public_patient_login"', output)
        self.assertIn('"outcome":"success"', output)
        self.assertNotIn(self.patient.national_id, output)
        self.assertNotIn(response.json()["access_token"], output)

    def test_missing_bearer_token_returns_401(self):
        for url_name in ("public_patient_me", "public_authenticated_patient_queue"):
            with self.subTest(url_name=url_name):
                response = self.client.get(reverse(url_name))
                self.assertEqual(response.status_code, 401)

    def test_invalid_and_expired_tokens_return_401(self):
        invalid = self.client.get(
            reverse("public_patient_me"),
            **self.bearer("not-a-valid-token"),
        )
        self.assertEqual(invalid.status_code, 401)

        token = self.login().json()["access_token"]
        PatientAccessToken.objects.filter(patient=self.patient).update(
            expires_at=timezone.now() - timedelta(seconds=1),
        )
        expired = self.client.get(reverse("public_patient_me"), **self.bearer(token))
        self.assertEqual(expired.status_code, 401)

    def test_me_returns_only_own_profile_with_masked_national_id(self):
        token = self.login().json()["access_token"]
        response = self.client.get(reverse("public_patient_me"), **self.bearer(token))

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["profile"]["first_name"], self.patient.first_name)
        self.assertEqual(payload["profile"]["national_id"], "1-xxxx-xxxxx-xx-3")
        self.assertEqual(payload["active_queue"]["queue_number"], "Q-10")
        self.assertEqual(len(payload["visits"]), 1)

    def test_queue_returns_latest_queue_for_token_owner(self):
        token = self.login().json()["access_token"]
        response = self.client.get(reverse("public_authenticated_patient_queue"), **self.bearer(token))

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["queue_number"], "Q-10")
        self.assertEqual(payload["status"], Queue.Status.WAITING_QUEUE)
        self.assertEqual(payload["queue_position"], 1)
        self.assertEqual(payload["room"], "ห้องตรวจ 2")
        self.assertIn("people_ahead", payload)
        self.assertIn("updated_at", payload)

    def test_patient_without_queue_returns_404(self):
        patient = Patient.objects.create(
            first_name="ไม่มี",
            last_name="คิว",
            national_id="2222222222222",
        )
        token = self.login(patient.national_id).json()["access_token"]
        response = self.client.get(reverse("public_authenticated_patient_queue"), **self.bearer(token))

        self.assertEqual(response.status_code, 404)
        self.assertFalse(response.json()["ok"])

    def test_token_cannot_read_another_patients_latest_queue(self):
        other = Patient.objects.create(
            first_name="คนอื่น",
            last_name="ทดสอบ",
            national_id="3333333333333",
        )
        other_visit = Visit.objects.create(patient=other)
        Queue.objects.create(visit=other_visit, status=Queue.Status.CALLED, exam_room=9)

        token = self.login().json()["access_token"]
        queue_response = self.client.get(reverse("public_authenticated_patient_queue"), **self.bearer(token))
        me_response = self.client.get(reverse("public_patient_me"), **self.bearer(token))

        self.assertEqual(queue_response.json()["queue_number"], "Q-10")
        self.assertEqual(other_visit.queue.display_number, "Q-11")
        self.assertNotEqual(queue_response.json()["queue_number"], other_visit.queue.display_number)
        self.assertEqual(me_response.json()["profile"]["first_name"], self.patient.first_name)
        self.assertNotIn(other.national_id, me_response.content.decode())

    def test_existing_registration_and_tracking_token_api_still_work(self):
        registration = self.post_json(reverse("public_patient_register"), {
            "first_name": "สายใจ",
            "last_name": "ทดสอบ",
            "national_id": "4444444444444",
            "gender": "F",
            "age": 28,
            "phone": "0899999999",
            "blood_type": "A",
            "note": "ปวดศีรษะ",
            "consent": True,
        })

        self.assertEqual(registration.status_code, 201)
        body = registration.json()
        self.assertTrue(body["tracking_token"])
        self.assertTrue(body["access_token"])
        legacy = self.client.get(
            reverse("public_patient_queue_status", args=[body["tracking_token"]]),
        )
        self.assertEqual(legacy.status_code, 200)
        self.assertEqual(legacy.json()["queue_number"], body["queue_number"])

    def test_cors_preflight_allows_configured_origin_and_authorization_header(self):
        response = self.client.options(
            reverse("public_patient_me"),
            HTTP_ORIGIN="https://patient.example.com",
        )
        blocked = self.client.options(
            reverse("public_patient_me"),
            HTTP_ORIGIN="https://untrusted.example.com",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Access-Control-Allow-Origin"], "https://patient.example.com")
        self.assertIn("Authorization", response["Access-Control-Allow-Headers"])
        self.assertNotIn("Access-Control-Allow-Origin", blocked)
