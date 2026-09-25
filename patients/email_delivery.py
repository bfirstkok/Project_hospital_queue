"""Transactional email delivery for patient-facing security flows.

Production can use Resend over HTTPS, which avoids SMTP egress restrictions on
some cloud providers. Tests/local development continue to use Django's configured
email backend when RESEND_API_KEY is not set.
"""

from __future__ import annotations

import json
import logging
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from django.conf import settings
from django.core.mail import send_mail as django_send_mail


logger = logging.getLogger("security.audit")


def _resend_sender(fallback: str | None = None) -> str:
    return str(
        getattr(settings, "RESEND_FROM_EMAIL", "")
        or fallback
        or getattr(settings, "DEFAULT_FROM_EMAIL", "")
    ).strip()


def _send_via_resend(
    subject: str,
    message: str,
    from_email: str | None,
    recipient_list,
) -> int:
    api_key = str(getattr(settings, "RESEND_API_KEY", "") or "").strip()
    if not api_key:
        raise RuntimeError("resend_api_key_missing")

    recipients = [str(item).strip() for item in recipient_list if str(item).strip()]
    if not recipients:
        return 0

    sender = _resend_sender(from_email)
    if not sender:
        raise RuntimeError("resend_from_email_missing")

    payload = json.dumps(
        {
            "from": sender,
            "to": recipients,
            "subject": subject,
            "text": message,
        },
        ensure_ascii=False,
    ).encode("utf-8")

    request = Request(
        str(getattr(settings, "RESEND_API_URL", "https://api.resend.com/emails")),
        data=payload,
        method="POST",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "hospital-queue/1.0",
        },
    )

    try:
        with urlopen(
            request,
            timeout=int(getattr(settings, "RESEND_TIMEOUT_SECONDS", 10)),
        ) as response:
            body = response.read().decode("utf-8")
            status = getattr(response, "status", 200)
    except HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8", errors="replace")[:1000]
        except Exception:
            pass
        logger.error("resend_http_error status=%s detail=%s", exc.code, detail)
        raise RuntimeError(f"resend_http_error:{exc.code}") from exc
    except URLError as exc:
        logger.error("resend_network_error reason=%s", exc.reason)
        raise RuntimeError("resend_network_error") from exc

    if status < 200 or status >= 300:
        raise RuntimeError(f"resend_unexpected_status:{status}")

    try:
        result = json.loads(body) if body else {}
    except json.JSONDecodeError:
        result = {}

    if not result.get("id"):
        logger.warning("resend_response_without_message_id")
    return 1


def send_transactional_email(
    subject,
    message,
    from_email,
    recipient_list,
    fail_silently=False,
    **kwargs,
):
    """Django send_mail-compatible adapter with Resend support.

    If RESEND_API_KEY exists, email is sent through Resend's HTTPS API.
    Otherwise Django's configured email backend is used. This preserves the
    locmem backend used by automated tests and local development.
    """
    api_key = str(getattr(settings, "RESEND_API_KEY", "") or "").strip()
    if not api_key:
        return django_send_mail(
            subject,
            message,
            from_email,
            recipient_list,
            fail_silently=fail_silently,
            **kwargs,
        )

    try:
        return _send_via_resend(subject, message, from_email, recipient_list)
    except Exception:
        if fail_silently:
            logger.exception("resend_delivery_failed_silently")
            return 0
        raise
