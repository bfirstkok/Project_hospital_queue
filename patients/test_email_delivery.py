import json
from unittest.mock import MagicMock, patch

from django.core import mail
from django.test import SimpleTestCase, override_settings

from .email_delivery import send_transactional_email


class TransactionalEmailDeliveryTests(SimpleTestCase):
    @override_settings(
        RESEND_API_KEY="re_test_key",
        RESEND_FROM_EMAIL="Hospital Queues <noreply@example.com>",
        RESEND_API_URL="https://api.resend.com/emails",
        RESEND_TIMEOUT_SECONDS=10,
    )
    @patch("patients.email_delivery.urlopen")
    def test_resend_sends_email_over_https(self, urlopen_mock):
        response = MagicMock()
        response.status = 200
        response.read.return_value = b'{"id":"email_123"}'
        urlopen_mock.return_value.__enter__.return_value = response

        sent = send_transactional_email(
            "OTP test",
            "รหัสของคุณคือ 123456",
            "ignored@example.com",
            ["patient@example.com"],
            fail_silently=False,
        )

        self.assertEqual(sent, 1)
        request = urlopen_mock.call_args.args[0]
        self.assertEqual(request.full_url, "https://api.resend.com/emails")
        self.assertEqual(request.get_header("Authorization"), "Bearer re_test_key")
        payload = json.loads(request.data.decode("utf-8"))
        self.assertEqual(payload["from"], "Hospital Queues <noreply@example.com>")
        self.assertEqual(payload["to"], ["patient@example.com"])
        self.assertEqual(payload["subject"], "OTP test")
        self.assertIn("123456", payload["text"])

    @override_settings(
        RESEND_API_KEY="",
        EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
        DEFAULT_FROM_EMAIL="Hospital <noreply@example.com>",
    )
    def test_without_resend_key_uses_django_mail_backend(self):
        mail.outbox = []

        sent = send_transactional_email(
            "Fallback test",
            "Local development message",
            "Hospital <noreply@example.com>",
            ["patient@example.com"],
            fail_silently=False,
        )

        self.assertEqual(sent, 1)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["patient@example.com"])
