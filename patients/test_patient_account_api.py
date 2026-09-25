import json
from unittest.mock import patch

from django.contrib.auth.hashers import check_password, make_password
from django.core import mail
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse

from .models import OtpChallenge, Patient, PatientAccessToken


@override_settings(
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    RESEND_API_KEY="",
    RESEND_FROM_EMAIL="",
    DEFAULT_FROM_EMAIL="Hospital <noreply@example.com>",
    PATIENT_APP_ORIGINS={"https://hospital.bfirstkok.me"},
    PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"],
    PATIENT_LOGIN_RATE_LIMIT=5,
    PATIENT_LOGIN_RATE_WINDOW=60,
    OTP_TTL_SECONDS=300,
    OTP_MAX_ATTEMPTS=5,
    OTP_REQUEST_LIMIT=3,
    OTP_REQUEST_WINDOW=900,
    PASSWORD_RESET_TOKEN_TTL_SECONDS=900,
)
class PatientAccountApiTests(TestCase):
    def setUp(self):
        cache.clear()

    def post_json(self, name, payload, token=None, **extra):
        if token:
            extra["HTTP_AUTHORIZATION"] = f"Bearer {token}"
        return self.client.post(
            reverse(name),
            data=json.dumps(payload),
            content_type="application/json",
            **extra,
        )

    def patch_json(self, name, payload, token):
        return self.client.patch(
            reverse(name),
            data=json.dumps(payload),
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {token}",
        )

    def create_account(self, **overrides):
        data = {
            "first_name": "สมชาย",
            "last_name": "ใจดี",
            "national_id": "1234567890123",
            "phone": "0812345678",
            "email": "somchai@example.com",
            "username": "somchai99",
            "password_hash": make_password("StrongPass#2026"),
        }
        data.update(overrides)
        return Patient.objects.create(**data)

    def test_register_creates_password_account_and_emergency_contacts(self):
        response = self.post_json(
            "public_patient_register",
            {
                "username": "newpatient",
                "password": "NewStrongPass#2026",
                "first_name": "ผู้ป่วย",
                "last_name": "ใหม่",
                "national_id": "2222222222222",
                "gender": "F",
                "age": 30,
                "phone": "+66891234567",
                "email": "NEW@example.com",
                "blood_type": "A",
                "note": "ตรวจทั่วไป",
                "emergency_contacts": [
                    {
                        "name": "ผู้ติดต่อ",
                        "relationship": "SPOUSE",
                        "phone": "0898765432",
                    }
                ],
                "consent": True,
                "website": None,
            },
        )

        self.assertEqual(response.status_code, 201)
        self.assertTrue(response.json()["access_token"])
        patient = Patient.objects.get(national_id="2222222222222")
        self.assertEqual(patient.username, "newpatient")
        self.assertEqual(patient.email, "new@example.com")
        self.assertEqual(patient.phone_normalized, "0891234567")
        self.assertTrue(check_password("NewStrongPass#2026", patient.password_hash))
        self.assertNotEqual(patient.password_hash, "NewStrongPass#2026")
        self.assertEqual(patient.emergency_contacts[0]["name"], "ผู้ติดต่อ")

    def test_password_login_accepts_username_email_and_national_id(self):
        patient = self.create_account()
        for identifier in (patient.username, patient.email, patient.national_id):
            with self.subTest(identifier=identifier):
                cache.clear()
                response = self.post_json(
                    "public_patient_login",
                    {"identifier": identifier, "password": "StrongPass#2026"},
                )
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()["profile"]["national_id"], patient.national_id)

        cache.clear()
        wrong = self.post_json(
            "public_patient_login",
            {"identifier": patient.username, "password": "wrong-password"},
        )
        self.assertEqual(wrong.status_code, 401)

    def test_password_login_rejects_google_only_account(self):
        patient = self.create_account(password_hash=None, google_id="google-sub-1", email_verified=True)
        response = self.post_json(
            "public_patient_login",
            {"identifier": patient.email, "password": "anything123"},
        )
        self.assertEqual(response.status_code, 401)

    def test_profile_patch_updates_editable_fields_and_rejects_identity_changes(self):
        patient = self.create_account()
        login = self.post_json(
            "public_patient_login",
            {"identifier": patient.username, "password": "StrongPass#2026"},
        )
        token = login.json()["access_token"]

        rejected = self.patch_json(
            "public_patient_me",
            {"national_id": "9999999999999"},
            token,
        )
        self.assertEqual(rejected.status_code, 400)
        self.assertIn("national_id", rejected.json()["errors"])

        updated = self.patch_json(
            "public_patient_me",
            {
                "phone": "+66899998888",
                "email": "changed@example.com",
                "blood_type": "O",
                "address": "99/1 ถนนทดสอบ",
                "province": "ขอนแก่น",
                "emergency_contacts": [
                    {"name": "ญาติ", "relationship": "RELATIVE", "phone": "0888888888"}
                ],
            },
            token,
        )
        self.assertEqual(updated.status_code, 200)
        profile = updated.json()["profile"]
        self.assertEqual(profile["national_id"], patient.national_id)
        self.assertEqual(profile["phone"], "0899998888")
        self.assertEqual(profile["email"], "changed@example.com")
        self.assertEqual(profile["emergency_contacts"][0]["name"], "ญาติ")

        patient.refresh_from_db()
        self.assertFalse(patient.email_verified)
        self.assertEqual(patient.phone_normalized, "0899998888")

    @patch("patients.views.send_mail", side_effect=RuntimeError("smtp down"))
    def test_password_reset_request_reports_email_delivery_failure(self, _send_mail):
        patient = self.create_account()

        response = self.post_json(
            "patient_password_reset_request",
            {"identifier": patient.email, "channel": "email"},
        )

        self.assertEqual(response.status_code, 503)
        self.assertFalse(response.json()["ok"])
        self.assertIn("ไม่สามารถส่งอีเมล OTP", response.json()["error"])
        challenge = OtpChallenge.objects.get()
        self.assertIsNotNone(challenge.consumed_at)

    @patch("patients.views.secrets.randbelow", return_value=123456)
    def test_password_reset_otp_changes_password_and_revokes_old_tokens(self, _randbelow):
        patient = self.create_account()
        login = self.post_json(
            "public_patient_login",
            {"identifier": patient.email, "password": "StrongPass#2026"},
        )
        old_token = login.json()["access_token"]
        self.assertTrue(PatientAccessToken.objects.filter(patient=patient).exists())

        requested = self.post_json(
            "patient_password_reset_request",
            {"identifier": patient.email, "channel": "email"},
        )
        self.assertEqual(requested.status_code, 200)
        self.assertTrue(requested.json()["masked_target"].startswith("so"))
        self.assertTrue(requested.json()["masked_target"].endswith("@example.com"))
        self.assertIn("123456", mail.outbox[0].body)

        verified = self.post_json(
            "patient_password_reset_verify_otp",
            {"identifier": patient.email, "otp": "123456"},
        )
        self.assertEqual(verified.status_code, 200)
        reset_token = verified.json()["reset_token"]

        confirmed = self.post_json(
            "patient_password_reset_confirm",
            {
                "identifier": patient.email,
                "reset_token": reset_token,
                "new_password": "ChangedStrong#2026",
                "confirm_password": "ChangedStrong#2026",
            },
        )
        self.assertEqual(confirmed.status_code, 200)

        patient.refresh_from_db()
        self.assertEqual(patient.token_version, 2)
        self.assertTrue(check_password("ChangedStrong#2026", patient.password_hash))
        self.assertFalse(PatientAccessToken.objects.filter(patient=patient).exists())
        self.assertIsNotNone(OtpChallenge.objects.get(purpose=OtpChallenge.Purpose.PASSWORD_RESET).consumed_at)

        stale = self.client.get(
            reverse("public_patient_me"),
            HTTP_AUTHORIZATION=f"Bearer {old_token}",
        )
        self.assertEqual(stale.status_code, 401)

        cache.clear()
        relogin = self.post_json(
            "public_patient_login",
            {"identifier": patient.username, "password": "ChangedStrong#2026"},
        )
        self.assertEqual(relogin.status_code, 200)

    def test_password_reset_request_does_not_enumerate_unknown_account(self):
        response = self.post_json(
            "patient_password_reset_request",
            {"identifier": "missing@example.com", "channel": "email"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["ok"])
        self.assertIsNone(response.json()["masked_target"])

    @patch("patients.views._verify_google_access_token")
    def test_google_oauth_popup_access_token_is_accepted(self, verify_google_access):
        patient = self.create_account(google_id=None, email_verified=False)
        verify_google_access.return_value = {
            "sub": "google-popup-sub-123",
            "email": patient.email,
            "email_verified": True,
            "given_name": "สมชาย",
            "family_name": "ป๊อปอัป",
        }

        response = self.post_json(
            "patient_google_auth",
            {"access_token": "google-oauth-access-token"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["access_token"])
        verify_google_access.assert_called_once_with("google-oauth-access-token")
        patient.refresh_from_db()
        self.assertEqual(patient.google_id, "google-popup-sub-123")
        self.assertTrue(patient.email_verified)

    @patch("patients.views._verify_google_credential")
    def test_google_auth_auto_links_by_verified_email(self, verify_google):
        patient = self.create_account(google_id=None, email_verified=False)
        verify_google.return_value = {
            "sub": "google-sub-123",
            "email": patient.email,
            "email_verified": True,
            "given_name": "สมชาย",
            "family_name": "ใจดี",
            "iss": "https://accounts.google.com",
        }

        response = self.post_json(
            "patient_google_auth",
            {"credential": "google-id-token"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["access_token"])
        patient.refresh_from_db()
        self.assertEqual(patient.google_id, "google-sub-123")
        self.assertTrue(patient.email_verified)

    @patch("patients.views._verify_google_credential")
    def test_google_new_user_receives_temp_link_token_usable_by_registration(self, verify_google):
        verify_google.return_value = {
            "sub": "new-google-sub",
            "email": "google-new@example.com",
            "email_verified": True,
            "given_name": "กูเกิล",
            "family_name": "ใหม่",
            "iss": "https://accounts.google.com",
        }
        google = self.post_json(
            "patient_google_auth",
            {"credential": "google-id-token"},
        )
        self.assertEqual(google.status_code, 200)
        self.assertTrue(google.json()["is_new_user"])

        registered = self.post_json(
            "public_patient_register",
            {
                "temp_token": google.json()["temp_token"],
                "first_name": "กูเกิล",
                "last_name": "ใหม่",
                "national_id": "3333333333333",
                "gender": "O",
                "age": 25,
                "phone": "0811111111",
                "email": "attacker@example.net",
                "blood_type": "UNKNOWN",
                "consent": True,
            },
        )
        self.assertEqual(registered.status_code, 201)
        patient = Patient.objects.get(national_id="3333333333333")
        self.assertEqual(patient.email, "google-new@example.com")
        self.assertEqual(patient.google_id, "new-google-sub")
        self.assertTrue(patient.email_verified)

    def test_unknown_patient_api_path_returns_json_envelope(self):
        response = self.client.get("/api/patient/not-a-real-endpoint/")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response["Content-Type"], "application/json")
        self.assertFalse(response.json()["ok"])
