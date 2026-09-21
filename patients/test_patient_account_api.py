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
    DEFAULT_FROM_EMAIL="Hospital <noreply@example.com>",
    PATIENT_APP_ORIGINS={"https://hospital.bfirstkok.me"},
    PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"],
    PATIENT_AUTH_RATE_LIMIT=20,
    PATIENT_AUTH_RATE_WINDOW=60,
    OTP_TTL_SECONDS=300,
    OTP_MAX_ATTEMPTS=5,
    GOOGLE_OAUTH_CLIENT_ID="google-client-id",
)
class PatientAccountApiTests(TestCase):
    def setUp(self):
        cache.clear()

    def post_json(self, name, payload, token=None):
        headers = {}
        if token:
            headers["HTTP_AUTHORIZATION"] = f"Bearer {token}"
        return self.client.post(
            reverse(name),
            data=json.dumps(payload),
            content_type="application/json",
            **headers,
        )

    def patch_json(self, name, payload, token):
        return self.client.patch(
            reverse(name),
            data=json.dumps(payload),
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {token}",
        )

    def create_account(self):
        return Patient.objects.create(
            first_name="สมชาย",
            last_name="ใจดี",
            national_id="1234567890123",
            username="somchai99",
            email="somchai@example.com",
            phone="0812345678",
            password_hash=make_password("SecurePassword#2026"),
        )

    def login(self, identifier, password="SecurePassword#2026"):
        return self.post_json(
            "public_patient_login",
            {"identifier": identifier, "password": password},
        )

    def test_register_stores_account_credentials_and_profile_fields(self):
        response = self.post_json(
            "public_patient_register",
            {
                "username": "newpatient",
                "password": "SecurePassword#2026",
                "email": "NEW@example.com",
                "phone": "+66891234567",
                "national_id": "2222222222222",
                "first_name": "ผู้ป่วย",
                "last_name": "ใหม่",
                "gender": "F",
                "age": 31,
                "blood_type": "O",
                "address": "99/1 ถนนทดสอบ",
                "province": "ขอนแก่น",
                "district": "เมืองขอนแก่น",
                "subdistrict": "ในเมือง",
                "postal_code": "40000",
                "emergency_contacts": [
                    {"name": "ญาติ ผู้ป่วย", "relationship": "SIBLING", "phone": "0890000000"}
                ],
                "consent": True,
                "website": None,
            },
        )

        self.assertEqual(response.status_code, 201)
        self.assertTrue(response.json()["ok"])
        patient = Patient.objects.get(national_id="2222222222222")
        self.assertEqual(patient.username, "newpatient")
        self.assertEqual(patient.email, "new@example.com")
        self.assertEqual(patient.phone, "0891234567")
        self.assertTrue(check_password("SecurePassword#2026", patient.password_hash))
        self.assertEqual(patient.address, "99/1 ถนนทดสอบ")
        self.assertEqual(patient.emergency_contacts[0]["name"], "ญาติ ผู้ป่วย")
        self.assertEqual(patient.emergency_name, "ญาติ ผู้ป่วย")

    def test_password_login_accepts_username_email_and_national_id(self):
        patient = self.create_account()

        for identifier in (patient.username, patient.email, patient.national_id):
            with self.subTest(identifier=identifier):
                response = self.login(identifier)
                self.assertEqual(response.status_code, 200)
                self.assertTrue(response.json()["access_token"])
                self.assertEqual(response.json()["profile"]["hn"], patient.hn)

        bad = self.login(patient.username, "wrong-password")
        self.assertEqual(bad.status_code, 401)
        self.assertFalse(bad.json()["ok"])

    def test_google_only_account_cannot_use_password_login(self):
        patient = Patient.objects.create(
            first_name="Google",
            last_name="Only",
            national_id="3333333333333",
            username="googleonly",
            email="google@example.com",
            google_id="google-sub",
            email_verified=True,
            password_hash=None,
        )
        response = self.login(patient.username, "anything-at-all")
        self.assertEqual(response.status_code, 401)

    def test_patch_profile_updates_editable_fields_and_rejects_immutable_identity(self):
        patient = self.create_account()
        token = self.login(patient.username).json()["access_token"]

        response = self.patch_json(
            "public_patient_me",
            {
                "phone": "+66899998888",
                "email": "updated@example.com",
                "address": "123 ถนนใหม่",
                "province": "ขอนแก่น",
                "blood_type": "AB",
                "height_cm": 176.5,
                "weight_kg": 72.0,
                "emergency_contacts": [
                    {"name": "สมหญิง ใจดี", "relationship": "SPOUSE", "phone": "0888888888"}
                ],
            },
            token,
        )

        self.assertEqual(response.status_code, 200)
        profile = response.json()["profile"]
        self.assertEqual(profile["phone"], "0899998888")
        self.assertEqual(profile["email"], "updated@example.com")
        self.assertEqual(profile["blood_type"], "AB")
        self.assertEqual(profile["address"], "123 ถนนใหม่")
        self.assertEqual(profile["emergency_contacts"][0]["name"], "สมหญิง ใจดี")

        immutable = self.patch_json(
            "public_patient_me",
            {"national_id": "9999999999999"},
            token,
        )
        self.assertEqual(immutable.status_code, 400)
        self.assertIn("national_id", immutable.json()["errors"])

    @patch("patients.views.secrets.randbelow", return_value=482910)
    def test_password_reset_otp_changes_password_and_revokes_old_tokens(self, _randbelow):
        patient = self.create_account()
        old_token = self.login(patient.username).json()["access_token"]

        requested = self.post_json(
            "patient_password_reset_request",
            {"identifier": patient.email, "channel": "email"},
        )
        self.assertEqual(requested.status_code, 200)
        self.assertTrue(requested.json()["ok"])
        self.assertTrue(requested.json()["masked_target"])
        self.assertIn("482910", mail.outbox[0].body)

        challenge = OtpChallenge.objects.get(purpose=OtpChallenge.Purpose.PASSWORD_RESET)
        self.assertNotEqual(challenge.code_hash, "482910")

        verified = self.post_json(
            "patient_password_reset_verify_otp",
            {"identifier": patient.email, "otp": "482910"},
        )
        self.assertEqual(verified.status_code, 200)
        reset_token = verified.json()["reset_token"]
        self.assertTrue(reset_token)

        confirmed = self.post_json(
            "patient_password_reset_confirm",
            {
                "reset_token": reset_token,
                "new_password": "NewSecurePassword#2026",
                "confirm_password": "NewSecurePassword#2026",
            },
        )
        self.assertEqual(confirmed.status_code, 200)

        patient.refresh_from_db()
        self.assertEqual(patient.token_version, 2)
        self.assertTrue(check_password("NewSecurePassword#2026", patient.password_hash))
        self.assertFalse(PatientAccessToken.objects.filter(patient=patient).exists())

        old_session = self.client.get(
            reverse("public_patient_me"),
            HTTP_AUTHORIZATION=f"Bearer {old_token}",
        )
        self.assertEqual(old_session.status_code, 401)

        old_password = self.login(patient.username, "SecurePassword#2026")
        new_password = self.login(patient.username, "NewSecurePassword#2026")
        self.assertEqual(old_password.status_code, 401)
        self.assertEqual(new_password.status_code, 200)

    def test_password_reset_request_does_not_enumerate_unknown_account(self):
        response = self.post_json(
            "patient_password_reset_request",
            {"identifier": "missing@example.com", "channel": "email"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["ok"])
        self.assertEqual(len(mail.outbox), 0)

    @patch("patients.views.google_id_token.verify_oauth2_token")
    def test_google_auth_auto_links_verified_email_and_issues_token(self, verify_token):
        patient = self.create_account()
        verify_token.return_value = {
            "sub": "google-sub-123",
            "email": patient.email,
            "email_verified": True,
            "given_name": "สมชาย",
            "family_name": "ใจดี",
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

    @patch("patients.views.google_id_token.verify_oauth2_token")
    def test_google_auth_new_user_returns_linking_token(self, verify_token):
        verify_token.return_value = {
            "sub": "new-google-sub",
            "email": "new-google@example.com",
            "email_verified": True,
            "given_name": "New",
            "family_name": "Patient",
        }

        response = self.post_json(
            "patient_google_auth",
            {"credential": "google-id-token"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["is_new_user"])
        self.assertTrue(response.json()["temp_token"])
        self.assertEqual(
            response.json()["suggested_profile"]["email"],
            "new-google@example.com",
        )
