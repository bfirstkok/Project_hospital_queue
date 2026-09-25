import json
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth.hashers import check_password
from django.core import mail
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .models import OtpChallenge, Patient, PatientPin


@override_settings(
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    RESEND_API_KEY="",
    RESEND_FROM_EMAIL="",
    DEFAULT_FROM_EMAIL="Hospital <noreply@example.com>",
    PATIENT_APP_ORIGINS={"https://patient.example.com"},
    PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"],
    OTP_TTL_SECONDS=300,
    OTP_MAX_ATTEMPTS=5,
    OTP_REQUEST_LIMIT=3,
    OTP_REQUEST_WINDOW=900,
    PIN_VERIFY_RATE_LIMIT=30,
    PIN_VERIFY_RATE_WINDOW=300,
    PIN_LOCKOUT_TIERS=[60, 300, 1800],
)
class PatientPinApiTests(TestCase):
    def setUp(self):
        cache.clear()
        self.patient = Patient.objects.create(
            first_name="ทดสอบ",
            last_name="ระบบ PIN",
            national_id="1234567890123",
            phone="0812345678",
            email="patient@example.com",
        )

    def post_json(self, name, payload, token=None, **extra):
        if token:
            extra["HTTP_AUTHORIZATION"] = f"Bearer {token}"
        return self.client.post(
            reverse(name),
            data=json.dumps(payload),
            content_type="application/json",
            **extra,
        )

    def login_token(self):
        response = self.post_json(
            "public_patient_login",
            {"national_id": self.patient.national_id},
        )
        return response.json()["access_token"]

    def setup_pin(self, pin="445566"):
        return self.post_json(
            "patient_pin_setup",
            {"pin": pin},
            token=self.login_token(),
        )

    def request_otp(self):
        return self.post_json(
            "patient_pin_reset_request",
            {
                "national_id": self.patient.national_id,
                "channel": "email",
                "target": "attacker-controlled@example.net",
            },
        )

    def test_setup_hashes_pin_and_verify_issues_new_token(self):
        setup = self.setup_pin()

        self.assertEqual(setup.status_code, 200)
        self.assertTrue(setup.json()["access_token"])
        pin_state = PatientPin.objects.get(patient=self.patient)
        self.assertNotEqual(pin_state.pin_hash, "445566")
        self.assertTrue(check_password("445566", pin_state.pin_hash))

        verified = self.post_json(
            "patient_pin_verify",
            {"national_id": self.patient.national_id, "pin": "445566"},
        )
        self.assertEqual(verified.status_code, 200)
        self.assertTrue(verified.json()["access_token"])

    def test_setup_requires_auth_and_six_digit_pin(self):
        missing_auth = self.post_json("patient_pin_setup", {"pin": "445566"})
        invalid = self.post_json(
            "patient_pin_setup",
            {"pin": "abc123"},
            token=self.login_token(),
        )
        self.assertEqual(missing_auth.status_code, 401)
        self.assertEqual(invalid.status_code, 400)

    def test_verify_lockout_escalates_and_login_does_not_reset_it(self):
        self.setup_pin()
        for expected_left in (2, 1, 0):
            response = self.post_json(
                "patient_pin_verify",
                {"national_id": self.patient.national_id, "pin": "000000"},
            )
            self.assertEqual(response.status_code, 401)
            self.assertEqual(response.json()["attempts_left"], expected_left)

        state = PatientPin.objects.get(patient=self.patient)
        self.assertEqual(state.lockout_level, 1)
        self.assertGreater(state.locked_until, timezone.now() + timedelta(seconds=50))
        locked = self.post_json(
            "patient_pin_verify",
            {"national_id": self.patient.national_id, "pin": "445566"},
        )
        self.assertEqual(locked.status_code, 423)

        self.login_token()
        state.refresh_from_db()
        self.assertEqual(state.lockout_level, 1)
        self.assertIsNotNone(state.locked_until)

        state.locked_until = timezone.now() - timedelta(seconds=1)
        state.save(update_fields=["locked_until"])
        for _ in range(3):
            self.post_json(
                "patient_pin_verify",
                {"national_id": self.patient.national_id, "pin": "000000"},
            )
        state.refresh_from_db()
        self.assertEqual(state.lockout_level, 2)
        self.assertGreater(state.locked_until, timezone.now() + timedelta(seconds=290))

    def test_correct_pin_resets_all_lockout_state(self):
        self.setup_pin()
        state = PatientPin.objects.get(patient=self.patient)
        state.failed_attempts = 2
        state.lockout_level = 2
        state.save(update_fields=["failed_attempts", "lockout_level"])

        response = self.post_json(
            "patient_pin_verify",
            {"national_id": self.patient.national_id, "pin": "445566"},
        )
        self.assertEqual(response.status_code, 200)
        state.refresh_from_db()
        self.assertEqual(state.failed_attempts, 0)
        self.assertEqual(state.lockout_level, 0)
        self.assertIsNone(state.locked_until)

    def test_change_pin_counts_wrong_current_pin_and_changes_valid_pin(self):
        self.setup_pin()
        token = self.login_token()
        wrong = self.post_json(
            "patient_pin_change",
            {"current_pin": "000000", "new_pin": "778899"},
            token=token,
        )
        self.assertEqual(wrong.status_code, 401)
        self.assertEqual(wrong.json()["attempts_left"], 2)

        changed = self.post_json(
            "patient_pin_change",
            {"current_pin": "445566", "new_pin": "778899"},
            token=token,
        )
        self.assertEqual(changed.status_code, 200)
        self.assertTrue(check_password("778899", PatientPin.objects.get(patient=self.patient).pin_hash))

    @patch("patients.views.send_mail", side_effect=RuntimeError("smtp down"))
    def test_otp_request_reports_email_delivery_failure(self, _send_mail):
        response = self.request_otp()

        self.assertEqual(response.status_code, 503)
        self.assertFalse(response.json()["ok"])
        self.assertIn("ไม่สามารถส่งอีเมล OTP", response.json()["error"])
        self.assertIsNotNone(OtpChallenge.objects.get().consumed_at)

    @patch("patients.views.secrets.randbelow", return_value=123456)
    def test_otp_request_uses_database_email_and_stores_only_hash(self, _randbelow):
        response = self.request_otp()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["resend_after_seconds"], 60)
        challenge = OtpChallenge.objects.get()
        self.assertNotEqual(challenge.code_hash, "123456")
        self.assertTrue(check_password("123456", challenge.code_hash))
        self.assertEqual(mail.outbox[0].to, ["patient@example.com"])
        self.assertIn("123456", mail.outbox[0].body)
        self.assertNotIn("attacker-controlled@example.net", mail.outbox[0].to)

    @patch("patients.views.secrets.randbelow", return_value=123456)
    def test_correct_otp_resets_pin_is_single_use_and_issues_token(self, _randbelow):
        self.setup_pin()
        self.request_otp()
        confirmed = self.post_json(
            "patient_pin_reset_confirm",
            {"national_id": self.patient.national_id, "otp": "123456", "pin": "778899"},
        )

        self.assertEqual(confirmed.status_code, 200)
        self.assertTrue(confirmed.json()["access_token"])
        self.assertTrue(check_password("778899", PatientPin.objects.get(patient=self.patient).pin_hash))
        self.assertIsNotNone(OtpChallenge.objects.get().consumed_at)
        reused = self.post_json(
            "patient_pin_reset_confirm",
            {"national_id": self.patient.national_id, "otp": "123456", "pin": "112233"},
        )
        self.assertEqual(reused.status_code, 400)

    @patch("patients.views.secrets.randbelow", return_value=123456)
    def test_wrong_otp_is_invalid_after_five_attempts(self, _randbelow):
        self.request_otp()
        for _ in range(5):
            response = self.post_json(
                "patient_pin_reset_confirm",
                {"national_id": self.patient.national_id, "otp": "000000", "pin": "778899"},
            )
        self.assertIn("ผิดเกินกำหนด", response.json()["error"])
        challenge = OtpChallenge.objects.get()
        self.assertEqual(challenge.attempts, 5)
        correct_after_limit = self.post_json(
            "patient_pin_reset_confirm",
            {"national_id": self.patient.national_id, "otp": "123456", "pin": "778899"},
        )
        self.assertEqual(correct_after_limit.status_code, 400)

    @patch("patients.views.secrets.randbelow", return_value=123456)
    def test_expired_otp_is_rejected(self, _randbelow):
        self.request_otp()
        OtpChallenge.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
        response = self.post_json(
            "patient_pin_reset_confirm",
            {"national_id": self.patient.national_id, "otp": "123456", "pin": "778899"},
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("หมดอายุ", response.json()["error"])

    def test_otp_request_does_not_enumerate_missing_patient_or_email(self):
        missing = self.post_json(
            "patient_pin_reset_request",
            {"national_id": "9999999999999", "channel": "email", "target": "x@example.com"},
            REMOTE_ADDR="10.0.0.1",
        )
        self.patient.email = None
        self.patient.save(update_fields=["email"])
        no_email = self.request_otp()
        self.assertEqual(missing.status_code, 200)
        self.assertTrue(missing.json()["ok"])
        self.assertEqual(no_email.status_code, 200)
        self.assertTrue(no_email.json()["ok"])
        self.assertEqual(len(mail.outbox), 0)

    @patch("patients.views.secrets.randbelow", return_value=123456)
    def test_fourth_otp_request_in_window_is_rate_limited(self, _randbelow):
        for _ in range(3):
            self.assertEqual(self.request_otp().status_code, 200)
        fourth = self.request_otp()
        self.assertEqual(fourth.status_code, 429)

    def test_phone_reset_reports_sms_unavailable(self):
        response = self.post_json(
            "patient_pin_reset_request",
            {"national_id": self.patient.national_id, "channel": "phone", "target": "0812345678"},
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("ยังไม่เปิดให้บริการ", response.json()["error"])

    def test_me_returns_email_and_normalized_phone(self):
        response = self.client.get(
            reverse("public_patient_me"),
            HTTP_AUTHORIZATION=f"Bearer {self.login_token()}",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["profile"]["email"], "patient@example.com")
        self.assertEqual(response.json()["profile"]["phone"], "0812345678")

    def test_registration_accepts_email_and_normalizes_thai_phone(self):
        response = self.post_json(
            "public_patient_register",
            {
                "first_name": "ผู้ป่วย",
                "last_name": "อีเมล",
                "national_id": "2222222222222",
                "gender": "F",
                "age": 30,
                "phone": "+66891234567",
                "email": "new-patient@example.com",
                "blood_type": "A",
                "note": "ตรวจทั่วไป",
                "consent": True,
            },
        )
        self.assertEqual(response.status_code, 201)
        patient = Patient.objects.get(national_id="2222222222222")
        self.assertEqual(patient.phone, "0891234567")
        self.assertEqual(patient.email, "new-patient@example.com")

    def test_new_pin_endpoint_cors_preflight_allows_authorization(self):
        response = self.client.options(
            reverse("patient_pin_setup"),
            HTTP_ORIGIN="https://patient.example.com",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Access-Control-Allow-Origin"], "https://patient.example.com")
        self.assertIn("Authorization", response["Access-Control-Allow-Headers"])
