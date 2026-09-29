import json
from datetime import timedelta

from django.core.cache import cache
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from queues.models import Queue, Visit, VitalSign
from opd.models import Bill, Prescription, VisitAssessment

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

    def test_login_success_returns_opaque_token_and_profile(self):
        response = self.login()

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["access_token"])
        self.assertEqual(payload["token_type"], "Bearer")
        self.assertEqual(payload["profile"]["national_id"], self.patient.national_id)
        stored = PatientAccessToken.objects.get(patient=self.patient)
        self.assertNotEqual(stored.token_hash, payload["access_token"])

    def test_login_rejects_unknown_patient(self):
        response = self.login("9999999999999")

        self.assertEqual(response.status_code, 401)
        self.assertFalse(response.json()["ok"])

    def test_login_rate_limit_blocks_request_after_five_attempts_per_minute(self):
        login_url = reverse("public_patient_login")
        for _ in range(5):
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
        self.assertEqual(payload["profile"]["national_id"], self.patient.national_id)
        self.assertEqual(payload["active_queue"]["queue_number"], "Q001")
        self.assertEqual(len(payload["visits"]), 1)

    def test_queue_returns_latest_queue_for_token_owner(self):
        token = self.login().json()["access_token"]
        response = self.client.get(reverse("public_authenticated_patient_queue"), **self.bearer(token))

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["queue_number"], "Q001")
        self.assertEqual(payload["status"], Queue.Status.WAITING_QUEUE)
        self.assertEqual(payload["queue_position"], 1)
        self.assertEqual(payload["room"], "ห้องตรวจ 2")
        self.assertIn("people_ahead", payload)
        self.assertIn("updated_at", payload)

    def test_patient_portal_advances_to_aftercare_when_payment_and_medicine_are_complete(self):
        staff = get_user_model().objects.create_user(username="portal-doctor", password="test-pass")
        VisitAssessment.objects.create(
            visit=self.visit,
            examiner=staff,
            diagnosis="ทดสอบ",
            treatment="รับยาและกลับบ้าน",
        )
        Prescription.objects.create(
            visit=self.visit,
            prescribed_by=staff,
            status=Prescription.Status.DISPENSED,
        )
        Bill.objects.create(visit=self.visit, status=Bill.Status.PAID)
        self.queue.status = Queue.Status.OPD_DONE
        self.queue.save(update_fields=["status"])

        token = self.login().json()["access_token"]
        headers = self.bearer(token)
        current_queue = self.client.get(reverse("public_authenticated_patient_queue"), **headers)
        profile = self.client.get(reverse("public_patient_me"), **headers)
        tracked_queue = self.client.get(
            reverse("public_patient_queue_status", args=[self.visit.tracking_token]),
        )

        expected_label = "พร้อมกลับบ้าน · รอปิด Visit"
        for response in (current_queue, tracked_queue):
            with self.subTest(endpoint=response.wsgi_request.path):
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()["status"], Queue.Status.OPD_DONE)
                self.assertEqual(response.json()["status_label"], expected_label)
                self.assertIn("รอเจ้าหน้าที่", response.json()["instruction"])
                journey = response.json()["patient_journey"]
                steps = {step["key"]: step for step in journey["steps"]}
                self.assertEqual(steps["billing"]["state"], "done")
                self.assertEqual(steps["billing"]["detail"], "ชำระแล้ว")
                self.assertEqual(steps["pharmacy"]["state"], "done")
                self.assertEqual(steps["pharmacy"]["detail"], "จ่ายยาแล้ว")
                self.assertEqual(steps["complete"]["state"], "current")
                self.assertNotIn("portal-doctor", response.content.decode())

        self.assertEqual(profile.status_code, 200)
        profile_payload = profile.json()
        self.assertEqual(profile_payload["active_queue"]["status_label"], expected_label)
        self.assertEqual(profile_payload["visits"][0]["status_label"], expected_label)
        self.assertIn("รอเจ้าหน้าที่", profile_payload["visits"][0]["status_detail"])
        self.assertIn("patient_journey", profile_payload["active_queue"])
        self.assertIn("patient_journey", profile_payload["visits"][0])

    def test_patient_queue_serializes_pending_billing_and_pharmacy_states(self):
        staff = get_user_model().objects.create_user(username="portal-doctor", password="test-pass")
        VisitAssessment.objects.create(visit=self.visit, examiner=staff, diagnosis="ทดสอบ")
        Prescription.objects.create(
            visit=self.visit,
            prescribed_by=staff,
            status=Prescription.Status.SENT,
        )
        Bill.objects.create(visit=self.visit, status=Bill.Status.READY)
        self.queue.status = Queue.Status.OPD_DONE
        self.queue.save(update_fields=["status"])

        token = self.login().json()["access_token"]
        response = self.client.get(
            reverse("public_authenticated_patient_queue"),
            **self.bearer(token),
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        steps = {step["key"]: step for step in payload["patient_journey"]["steps"]}
        self.assertEqual(payload["patient_journey"]["current_label"], "การเงิน")
        self.assertEqual(steps["billing"]["state"], "current")
        self.assertEqual(steps["billing"]["detail"], "รอชำระเงิน")
        self.assertEqual(steps["pharmacy"]["state"], "current")
        self.assertEqual(steps["pharmacy"]["detail"], "รอห้องยา")
        self.assertNotIn("portal-doctor", response.content.decode())

    def test_patient_can_cancel_own_waiting_or_called_queue(self):
        other_patient = Patient.objects.create(
            first_name="ผู้ป่วยอื่น",
            last_name="ทดสอบ",
            national_id="5555555555555",
        )
        other_visit = Visit.objects.create(patient=other_patient)
        other_queue = Queue.objects.create(visit=other_visit, status=Queue.Status.CALLED)
        self.queue.status = Queue.Status.CALLED
        self.queue.save(update_fields=["status"])
        token = self.login().json()["access_token"]

        response = self.client.post(
            reverse("public_patient_cancel_queue"),
            **self.bearer(token),
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["ok"])
        self.queue.refresh_from_db()
        other_queue.refresh_from_db()
        self.assertEqual(self.queue.status, Queue.Status.CANCELLED)
        self.assertEqual(other_queue.status, Queue.Status.CALLED)
        current_queue = self.client.get(
            reverse("public_authenticated_patient_queue"),
            **self.bearer(token),
        )
        self.assertEqual(current_queue.status_code, 200)
        self.assertTrue(current_queue.json()["ok"])
        self.assertIsNone(current_queue.json()["queue_number"])

    def test_patient_cancel_requires_token_and_rejects_non_cancellable_status(self):
        missing_token = self.client.post(reverse("public_patient_cancel_queue"))
        self.assertEqual(missing_token.status_code, 401)

        self.queue.status = Queue.Status.EMERGENCY_TRANSFER
        self.queue.save(update_fields=["status"])
        token = self.login().json()["access_token"]
        response = self.client.post(
            reverse("public_patient_cancel_queue"),
            **self.bearer(token),
        )

        self.assertEqual(response.status_code, 409)
        self.queue.refresh_from_db()
        self.assertEqual(self.queue.status, Queue.Status.EMERGENCY_TRANSFER)

    def test_patient_without_queue_returns_empty_success(self):
        patient = Patient.objects.create(
            first_name="ไม่มี",
            last_name="คิว",
            national_id="2222222222222",
        )
        token = self.login(patient.national_id).json()["access_token"]
        response = self.client.get(reverse("public_authenticated_patient_queue"), **self.bearer(token))

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["ok"])
        self.assertIsNone(response.json()["queue_number"])

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

        self.assertEqual(queue_response.json()["queue_number"], "Q001")
        self.assertEqual(other_visit.queue.display_number, "Q002")
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
        self.assertIn("X-Requested-With", response["Access-Control-Allow-Headers"])
        self.assertNotIn("Access-Control-Allow-Origin", blocked)
