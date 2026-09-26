# patients/views.py
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.hashers import check_password, make_password
from django.contrib.auth.password_validation import validate_password
from django.conf import settings
from django.core import signing
from django.core.exceptions import ObjectDoesNotExist, ValidationError
from django.core.validators import validate_email
from django.db.models import Prefetch, Q
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, render, redirect
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from accounts.access import Capability, has_capability
from datetime import timedelta
import hashlib
import json
import logging
import re
import secrets
from urllib.parse import urlencode
from urllib.request import Request as UrlRequest, urlopen

from .email_delivery import send_transactional_email as send_mail
from .forms import PatientBirthDateForm, PatientForm, PublicPatientRegistrationForm, normalize_thai_phone
from .models import Appointment, OtpChallenge, Patient, PatientAccessToken, PatientPin
from .security import rate_limited, rate_limited_by_identifier
from queues.models import Visit, Queue, VitalSign, VisitWorkflowLog


ACTIVE_QUEUE_STATUSES = {
    Queue.Status.WAITING_VITALS,
    Queue.Status.WAITING_CONFIRMATION,
    Queue.Status.WAITING_QUEUE,
    Queue.Status.WAITING,
    Queue.Status.CALLED,
    Queue.Status.MONITORING,
    Queue.Status.OBSERVATION_MONITORING,
    Queue.Status.REASSESSMENT_REQUIRED,
    Queue.Status.EMERGENCY_TRANSFER,
    Queue.Status.FOLLOWUP,
}

security_logger = logging.getLogger("security.audit")
PIN_PATTERN = re.compile(r"[0-9]{6}")
USERNAME_PATTERN = re.compile(r"[A-Za-z0-9._-]{3,50}")
GOOGLE_LINK_SALT = "patient-google-link-v1"

PATIENT_CANCELLABLE_QUEUE_STATUSES = {
    Queue.Status.WAITING_VITALS,
    Queue.Status.WAITING_CONFIRMATION,
    Queue.Status.WAITING_QUEUE,
    Queue.Status.WAITING,
    Queue.Status.CALLED,
}

PUBLIC_STATUS = {
    Queue.Status.WAITING_VITALS: ("รอตรวจวัดสัญญาณชีพ", "กรุณาไปยังจุดวัดสัญญาณชีพ"),
    Queue.Status.WAITING_CONFIRMATION: ("รอพยาบาลยืนยันผลคัดกรอง", "กรุณารอบริเวณจุดคัดกรอง"),
    Queue.Status.WAITING_QUEUE: ("รอเรียกคิว", "กรุณารอบริเวณหน้าห้องตรวจ"),
    Queue.Status.WAITING: ("รอเรียกคิว", "กรุณารอบริเวณหน้าห้องตรวจ"),
    Queue.Status.CALLED: ("กรุณาเข้าห้องตรวจ", "ถึงคิวของคุณแล้ว กรุณาเข้าห้องตรวจ"),
    Queue.Status.MONITORING: ("ติดตามอาการหลังตรวจ", "อยู่ระหว่างการติดตามอาการตามแผนการรักษา"),
    Queue.Status.OBSERVATION_MONITORING: ("กำลังเฝ้าระวัง", "กำลังติดตามสัญญาณชีพด้วยอุปกรณ์"),
    Queue.Status.REASSESSMENT_REQUIRED: ("รอพยาบาลประเมินซ้ำ", "อุปกรณ์พบค่าที่ต้องตรวจสอบ กรุณารอพยาบาล"),
    Queue.Status.EMERGENCY_TRANSFER: ("ส่งต่อฉุกเฉิน", "กรุณาปฏิบัติตามคำแนะนำของบุคลากรทันที"),
    Queue.Status.OPD_DONE: ("แพทย์ตรวจเสร็จ", "กรุณาดำเนินการตามขั้นตอนหลังตรวจ เช่น ห้องยา/การเงิน หากมี"),
    Queue.Status.FOLLOWUP: ("นัดติดตามอาการ", "กรุณาตรวจสอบวันนัดกับเจ้าหน้าที่"),
    Queue.Status.DISCHARGED: ("เสร็จสิ้นการรับบริการ", "สามารถกลับบ้านได้ตามคำแนะนำของเจ้าหน้าที่"),
    Queue.Status.CANCELLED: ("ยกเลิกคิวแล้ว", "หากต้องการรับบริการ กรุณาติดต่อเจ้าหน้าที่"),
}


def _patient_journey_for_visit(visit, workflow_logs=None):
    """Build one cross-department patient journey from the evidence stored on a Visit.

    OPD_DONE means the doctor's examination is finished; it is intentionally not
    treated as the end of the whole hospital journey when pharmacy/billing work
    remains.
    """
    workflow_logs = list(workflow_logs or [])

    try:
        queue = visit.queue
    except ObjectDoesNotExist:
        queue = None
    try:
        triage = visit.triage_result
    except ObjectDoesNotExist:
        triage = None
    try:
        assessment = visit.opd_assessment
    except ObjectDoesNotExist:
        assessment = None
    try:
        prescription = visit.prescription
    except ObjectDoesNotExist:
        prescription = None
    try:
        bill = visit.bill
    except ObjectDoesNotExist:
        bill = None

    queue_status = getattr(queue, "status", "")
    logged_events = {log.event_type for log in workflow_logs}

    def step(key, label, state, detail):
        return {
            "key": key,
            "label": label,
            "state": state,
            "detail": detail,
        }

    beyond_vitals = queue_status not in {"", Queue.Status.WAITING_VITALS}
    vitals_done = (
        VisitWorkflowLog.EventType.VITALS_RECORDED in logged_events
        or bool(visit.triaged_at)
        or beyond_vitals
    )

    triage_done = bool(
        visit.confirmed_at
        or visit.final_severity
        or getattr(triage, "nurse_severity", None)
    )

    queue_done = bool(
        visit.called_at
        or queue_status in {
            Queue.Status.CALLED,
            Queue.Status.MONITORING,
            Queue.Status.OBSERVATION_MONITORING,
            Queue.Status.REASSESSMENT_REQUIRED,
            Queue.Status.EMERGENCY_TRANSFER,
            Queue.Status.OPD_DONE,
            Queue.Status.FOLLOWUP,
            Queue.Status.DISCHARGED,
        }
    )

    doctor_done = assessment is not None

    prescription_status = getattr(prescription, "status", "")
    pharmacy_done = prescription_status == "DISPENSED"
    pharmacy_skipped = prescription_status == "CANCELLED" or (
        prescription is None and bill is not None and doctor_done
    )
    pharmacy_started = prescription_status in {"DRAFT", "SENT", "PREPARING", "READY"}

    bill_status = getattr(bill, "status", "")
    billing_done = bill_status in {"PAID", "WAIVED"}
    billing_cancelled = bill_status == "CANCELLED"
    billing_started = bill_status in {"DRAFT", "READY"}

    terminal_cancelled = queue_status == Queue.Status.CANCELLED
    emergency_transfer = queue_status == Queue.Status.EMERGENCY_TRANSFER
    downstream_complete = (
        doctor_done
        and (pharmacy_done or pharmacy_skipped)
        and billing_done
    )
    completed = queue_status == Queue.Status.DISCHARGED or downstream_complete

    steps = [
        step(
            "registration",
            "ลงทะเบียน",
            "done",
            f"ลงทะเบียน {timezone.localtime(visit.registered_at).strftime('%d/%m/%Y %H:%M')}" if visit.registered_at else "ลงทะเบียนแล้ว",
        ),
        step(
            "vitals",
            "วัดสัญญาณชีพ",
            "done" if vitals_done else ("current" if queue_status == Queue.Status.WAITING_VITALS else "pending"),
            "บันทึกสัญญาณชีพแล้ว" if vitals_done else "รอวัดและบันทึกสัญญาณชีพ",
        ),
        step(
            "triage",
            "คัดกรอง",
            "done" if triage_done else ("current" if queue_status == Queue.Status.WAITING_CONFIRMATION else "pending"),
            (
                f"ยืนยันระดับ {visit.get_final_severity_display()}"
                if triage_done and visit.final_severity
                else ("รอพยาบาลยืนยันผลคัดกรอง" if not triage_done else "คัดกรองแล้ว")
            ),
        ),
        step(
            "queue",
            "รอ/เรียกคิว",
            "done" if queue_done else ("current" if queue_status in {Queue.Status.WAITING_QUEUE, Queue.Status.WAITING} else "pending"),
            (
                f"เรียกคิวแล้ว · {queue.display_number}" if queue_done and queue
                else (f"กำลังรอเรียก · {queue.display_number}" if queue else "รอเข้าคิวบริการ")
            ),
        ),
        step(
            "doctor",
            "ห้องตรวจ",
            "done" if doctor_done else ("current" if queue_status == Queue.Status.CALLED else "pending"),
            (
                f"แพทย์ตรวจแล้ว · {assessment.examiner.get_full_name() or assessment.examiner.username}"
                if doctor_done and getattr(assessment, "examiner", None)
                else ("แพทย์บันทึกผลตรวจแล้ว" if doctor_done else "รอเข้าห้องตรวจ")
            ),
        ),
        step(
            "pharmacy",
            "ห้องยา",
            "done" if pharmacy_done else (
                "skipped" if pharmacy_skipped else (
                    "current" if pharmacy_started and doctor_done else "pending"
                )
            ),
            (
                "จ่ายยาแล้ว"
                if pharmacy_done else (
                    "ไม่มีรายการยาที่ต้องรับ" if pharmacy_skipped else (
                        prescription.get_status_display() if prescription else "รอแผนหลังตรวจ/ใบสั่งยา"
                    )
                )
            ),
        ),
        step(
            "billing",
            "การเงิน",
            "done" if billing_done else (
                "skipped" if billing_cancelled else (
                    "current" if billing_started and doctor_done else "pending"
                )
            ),
            (
                bill.get_status_display()
                if bill else "รอสรุปค่าใช้จ่ายหลังตรวจ"
            ),
        ),
        step(
            "complete",
            "เสร็จสิ้น",
            "done" if completed else (
                "cancelled" if terminal_cancelled else (
                    "current" if emergency_transfer else "pending"
                )
            ),
            (
                "กระบวนการบริการเสร็จสิ้นแล้ว"
                if completed else (
                    "ยกเลิกคิว/การรับบริการ"
                    if terminal_cancelled else (
                        "อยู่ระหว่างส่งต่อฉุกเฉิน"
                        if emergency_transfer else "ยังมีขั้นตอนที่ต้องดำเนินการต่อ"
                    )
                )
            ),
        ),
    ]

    if terminal_cancelled:
        current_label = "ยกเลิกการรับบริการ"
        current_detail = "คิวนี้ถูกยกเลิก"
        current_class = "cancelled"
    elif emergency_transfer:
        current_label = "ส่งต่อฉุกเฉิน"
        current_detail = "ผู้ป่วยอยู่ในกระบวนการดูแลฉุกเฉิน"
        current_class = "urgent"
    elif completed:
        current_label = "เสร็จสิ้นการรับบริการ"
        current_detail = "ขั้นตอนที่ต้องดำเนินการของ Visit นี้เสร็จแล้ว"
        current_class = "done"
    elif billing_started:
        current_label = "การเงิน"
        current_detail = bill.get_status_display() if bill else "กำลังดำเนินการ"
        current_class = "current"
    elif pharmacy_started:
        current_label = "ห้องยา"
        current_detail = prescription.get_status_display() if prescription else "กำลังดำเนินการ"
        current_class = "current"
    elif doctor_done:
        current_label = "ขั้นตอนหลังตรวจ"
        if prescription is None and bill is None:
            current_detail = "แพทย์ตรวจเสร็จแล้ว · รอส่งต่อห้องยา/การเงินตามแผนการรักษา"
        else:
            current_detail = "แพทย์ตรวจเสร็จแล้ว · กำลังดำเนินการหลังตรวจ"
        current_class = "current"
    elif queue_status == Queue.Status.CALLED:
        current_label = "ห้องตรวจ"
        current_detail = "ถึงคิวแล้ว · รอแพทย์ตรวจ"
        current_class = "current"
    elif queue_status in {Queue.Status.OBSERVATION_MONITORING, Queue.Status.REASSESSMENT_REQUIRED}:
        current_label = "เฝ้าระวัง"
        current_detail = "กำลังติดตามสัญญาณชีพ/ตรวจอาการระหว่างรอ"
        current_class = "current"
    elif queue_status in {Queue.Status.WAITING_QUEUE, Queue.Status.WAITING}:
        current_label = "รอเรียกคิว"
        current_detail = queue.display_number if queue else "กำลังรอ"
        current_class = "current"
    elif queue_status == Queue.Status.WAITING_CONFIRMATION:
        current_label = "คัดกรอง"
        current_detail = "รอพยาบาลยืนยันผล"
        current_class = "current"
    else:
        current_label = "วัดสัญญาณชีพ"
        current_detail = "รอบันทึกข้อมูลก่อนคัดกรอง"
        current_class = "current"

    return {
        "steps": steps,
        "current_label": current_label,
        "current_detail": current_detail,
        "current_class": current_class,
        "queue_status": queue_status,
        "queue_status_label": (
            PUBLIC_STATUS.get(queue_status, (queue.get_status_display(), ""))[0]
            if queue else "ไม่พบข้อมูลคิว"
        ),
    }


def _cors_json(request, payload, status=200):
    payload = dict(payload)
    if status >= 400:
        payload.setdefault("ok", False)
        payload.setdefault("error", "ไม่สามารถดำเนินการตามคำขอได้")
    else:
        payload.setdefault("ok", True)
    response = JsonResponse(payload, status=status)
    origin = request.headers.get("Origin", "")
    if origin and origin in settings.PATIENT_APP_ORIGINS:
        response["Access-Control-Allow-Origin"] = origin
        from django.utils.cache import patch_vary_headers
        patch_vary_headers(response, ["Origin"])
    response["Access-Control-Allow-Methods"] = "GET, POST, PATCH, OPTIONS"
    response["Access-Control-Allow-Headers"] = "Content-Type, Authorization, X-Requested-With"
    response["Access-Control-Allow-Credentials"] = "true"
    response["Access-Control-Max-Age"] = "86400"
    return response


def _queue_number(queue):
    return queue.display_number


def _json_body(request):
    if int(request.META.get("CONTENT_LENGTH") or 0) > 16384:
        return None, "ข้อมูลมีขนาดใหญ่เกินไป", 413
    try:
        payload = json.loads(request.body.decode("utf-8") or "{}")
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None, "รูปแบบข้อมูลไม่ถูกต้อง", 400
    if not isinstance(payload, dict):
        return None, "รูปแบบข้อมูลไม่ถูกต้อง", 400
    return payload, None, None


def _token_digest(raw_token):
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def _issue_patient_token(patient):
    raw_token = secrets.token_urlsafe(32)
    token_ttl = int(getattr(settings, "PATIENT_TOKEN_MAX_AGE", 60 * 60 * 12))
    expires_at = timezone.now() + timedelta(seconds=token_ttl)
    PatientAccessToken.objects.create(
        patient=patient,
        token_hash=_token_digest(raw_token),
        token_version=patient.token_version,
        expires_at=expires_at,
    )
    return raw_token, expires_at


def _authenticated_patient(request):
    authorization = request.headers.get("Authorization", "")
    parts = authorization.split()
    if len(parts) != 2 or parts[0].lower() != "bearer" or not parts[1]:
        return None

    access_token = (
        PatientAccessToken.objects.select_related("patient")
        .filter(token_hash=_token_digest(parts[1]))
        .first()
    )
    now = timezone.now()
    if (
        not access_token
        or access_token.expires_at <= now
        or not access_token.patient.is_active
        or access_token.token_version != access_token.patient.token_version
    ):
        return None
    if not access_token.last_used_at or access_token.last_used_at < now - timedelta(minutes=5):
        PatientAccessToken.objects.filter(pk=access_token.pk).update(last_used_at=now)
    return access_token.patient


def _pin_is_locked(pin_state, now=None):
    now = now or timezone.now()
    if pin_state.locked_until and pin_state.locked_until > now:
        return True
    if pin_state.locked_until:
        pin_state.locked_until = None
        pin_state.failed_attempts = 0
        pin_state.save(update_fields=["locked_until", "failed_attempts", "updated_at"])
    return False


def _record_pin_failure(pin_state):
    pin_state.failed_attempts += 1
    locked_until = None
    if pin_state.failed_attempts >= 3:
        tiers = list(getattr(settings, "PIN_LOCKOUT_TIERS", [60, 300, 1800])) or [60, 300, 1800]
        duration = tiers[min(pin_state.lockout_level, len(tiers) - 1)]
        locked_until = timezone.now() + timedelta(seconds=duration)
        pin_state.locked_until = locked_until
        pin_state.lockout_level += 1
        pin_state.failed_attempts = 0
    pin_state.save(
        update_fields=["failed_attempts", "locked_until", "lockout_level", "updated_at"]
    )
    return 3 - pin_state.failed_attempts if locked_until is None else 0, locked_until


def _reset_pin_state(pin_state, new_pin=None):
    if new_pin is not None:
        pin_state.pin_hash = make_password(new_pin)
    pin_state.failed_attempts = 0
    pin_state.locked_until = None
    pin_state.lockout_level = 0
    fields = ["failed_attempts", "locked_until", "lockout_level", "updated_at"]
    if new_pin is not None:
        fields.insert(0, "pin_hash")
    pin_state.save(update_fields=fields)


def _locked_response(request, pin_state):
    return _cors_json(
        request,
        {
            "ok": False,
            "error": "ถูกระงับชั่วคราว",
            "locked_until": pin_state.locked_until.isoformat(),
        },
        status=423,
    )


def _masked_national_id(national_id):
    value = str(national_id or "")
    if len(value) != 13:
        return ""
    return f"{value[0]}-xxxx-xxxxx-xx-{value[-1]}"


def _normalize_email(value):
    return str(value or "").strip().lower()


def _normalize_phone(value):
    phone = "".join(ch for ch in str(value or "") if ch.isdigit() or ch == "+")
    if phone.startswith("+66"):
        phone = "0" + phone[3:]
    elif phone.startswith("66") and len(phone) == 11:
        phone = "0" + phone[2:]
    return phone


def _mask_email(value):
    email = _normalize_email(value)
    if not email or "@" not in email:
        return None
    local, domain = email.split("@", 1)
    visible = local[:2] if len(local) >= 2 else local[:1]
    hidden_count = max(4, len(local) - len(visible))
    return f"{visible}{'•' * hidden_count}@{domain}"


def _mask_phone(value):
    phone = _normalize_phone(value)
    if len(phone) < 4:
        return None
    return f"{phone[:3]}{'•' * max(4, len(phone) - 5)}{phone[-2:]}"


def _verify_google_credential(credential):
    """Verify a Google ID token and return its claims.

    Imported lazily so the rest of the patient API stays usable even when
    Google Sign-In is not configured.
    """
    client_id = str(getattr(settings, "GOOGLE_CLIENT_ID", "") or "").strip()
    if not client_id:
        raise RuntimeError("google_not_configured")
    from google.auth.transport import requests as google_requests
    from google.oauth2 import id_token

    claims = id_token.verify_oauth2_token(
        credential,
        google_requests.Request(),
        client_id,
    )
    if claims.get("iss") not in {"accounts.google.com", "https://accounts.google.com"}:
        raise ValueError("invalid_issuer")
    return claims


def _google_json_request(url, *, headers=None):
    request = UrlRequest(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "hospital-patient-google-auth/1.0",
            **(headers or {}),
        },
    )
    with urlopen(request, timeout=8) as response:
        return json.loads(response.read().decode("utf-8"))


def _verify_google_access_token(access_token):
    """Verify an OAuth access token issued to this web client and return user claims.

    The browser token-client flow gives us a real popup UX. The backend still
    validates that the token was issued to our configured client before using
    Google's UserInfo response as an identity assertion.
    """
    client_id = str(getattr(settings, "GOOGLE_CLIENT_ID", "") or "").strip()
    if not client_id:
        raise RuntimeError("google_not_configured")

    token_info = _google_json_request(
        "https://oauth2.googleapis.com/tokeninfo?"
        + urlencode({"access_token": access_token})
    )
    audience = str(
        token_info.get("aud")
        or token_info.get("audience")
        or token_info.get("issued_to")
        or ""
    ).strip()
    if audience != client_id:
        raise ValueError("invalid_audience")

    scopes = set(str(token_info.get("scope") or "").split())
    if "openid" not in scopes or "email" not in scopes:
        raise ValueError("missing_identity_scope")

    claims = _google_json_request(
        "https://openidconnect.googleapis.com/v1/userinfo",
        headers={"Authorization": f"Bearer {access_token}"},
    )
    token_subject = str(
        token_info.get("sub") or token_info.get("user_id") or ""
    ).strip()
    claim_subject = str(claims.get("sub") or "").strip()
    if not claim_subject or (token_subject and token_subject != claim_subject):
        raise ValueError("subject_mismatch")

    verified = claims.get("email_verified")
    claims["email_verified"] = (
        verified is True or str(verified).strip().lower() == "true"
    )
    return claims


def _lookup_patient_by_identifier(identifier):
    value = str(identifier or "").strip()
    if not value:
        return None

    by_national_id = Patient.objects.filter(national_id=value).first()
    if by_national_id:
        return by_national_id

    if "@" in value:
        matches = Patient.objects.filter(email__iexact=value)
        return matches.first() if matches.count() == 1 else None

    matches = Patient.objects.filter(username__iexact=value)
    if matches.count() == 1:
        return matches.first()

    normalized_phone = _normalize_phone(value)
    if normalized_phone:
        matches = Patient.objects.filter(
            Q(phone_normalized=normalized_phone) | Q(phone=normalized_phone)
        ).distinct()
        if matches.count() == 1:
            return matches.first()
    return None


def _validate_portal_password(password):
    value = str(password or "")
    if len(value) < 8 or len(value) > 128:
        return "รหัสผ่านต้องมีความยาวอย่างน้อย 8 ตัวอักษร"
    try:
        validate_password(value)
    except ValidationError:
        return "รหัสผ่านยังไม่ปลอดภัยเพียงพอ กรุณาใช้รหัสผ่านที่คาดเดาได้ยากขึ้น"
    return None


def _patient_profile_payload(patient, *, mask_national_id=True):
    contacts = patient.emergency_contacts or []
    if not contacts and (patient.emergency_name or patient.emergency_phone):
        contacts = [{
            "id": "primary",
            "name": patient.emergency_name,
            "relationship": patient.emergency_relationship,
            "phone": patient.emergency_phone,
        }]
    return {
        "username": patient.username,
        "first_name": patient.first_name,
        "last_name": patient.last_name,
        "national_id": _masked_national_id(patient.national_id) if mask_national_id else patient.national_id,
        "hn": patient.hn,
        "phone": patient.phone,
        "email": patient.email,
        "gender": patient.gender,
        "birth_date": patient.birth_date.isoformat() if patient.birth_date else None,
        "age": patient.age_years,
        "age_display": patient.age_display,
        "blood_type": patient.blood_type,
        "height_cm": float(patient.height_cm) if patient.height_cm is not None else None,
        "weight_kg": float(patient.weight_kg) if patient.weight_kg is not None else None,
        "address": patient.address,
        "province": patient.province,
        "district": patient.district,
        "subdistrict": patient.subdistrict,
        "postal_code": patient.postal_code,
        "chronic_diseases": patient.chronic_diseases,
        "allergies": patient.allergies,
        "medications": patient.medications,
        "emergency_name": patient.emergency_name,
        "emergency_relationship": patient.emergency_relationship,
        "emergency_phone": patient.emergency_phone,
        "emergency_contacts": contacts,
        "email_verified": patient.email_verified,
    }


def _revoke_patient_tokens(patient):
    patient.token_version += 1
    patient.save(update_fields=["token_version", "updated_at"])
    PatientAccessToken.objects.filter(patient=patient).delete()


def _validate_emergency_contacts(value):
    if value is None:
        return None, None
    if not isinstance(value, list):
        return None, "emergency_contacts ต้องเป็นรายการ"
    cleaned = []
    for index, contact in enumerate(value):
        if not isinstance(contact, dict):
            return None, f"ข้อมูลผู้ติดต่อฉุกเฉินลำดับที่ {index + 1} ไม่ถูกต้อง"
        name = str(contact.get("name") or "").strip()
        phone = _normalize_phone(contact.get("phone"))
        relationship = str(contact.get("relationship") or "").strip().upper()
        if not name or not phone:
            return None, f"กรุณาระบุชื่อและเบอร์โทรผู้ติดต่อฉุกเฉินลำดับที่ {index + 1}"
        cleaned.append({
            "id": str(contact.get("id") or f"em_{index + 1}"),
            "name": name[:120],
            "relationship": relationship[:20],
            "phone": phone[:20],
        })
    return cleaned, None


def _serialize_queue(queue):
    label, instruction = PUBLIC_STATUS.get(
        queue.status,
        (queue.get_status_display(), "กรุณาติดต่อเจ้าหน้าที่"),
    )
    return {
        "queue_number": _queue_number(queue),
        "status": queue.status,
        "status_label": label,
        "instruction": instruction,
        "room": f"ห้องตรวจ {queue.exam_room}" if queue.exam_room else None,
        "people_ahead": _people_ahead(queue),
        "queue_position": _queue_position(queue),
        "updated_at": timezone.now().isoformat(),
    }


def _serialize_vitals(visit):
    try:
        vitals = visit.vitals
    except VitalSign.DoesNotExist:
        return None
    return {
        "rr": vitals.rr,
        "pr": vitals.pr,
        "sys_bp": vitals.sys_bp,
        "dia_bp": vitals.dia_bp,
        "bt": vitals.bt,
        "o2sat": vitals.o2sat,
        "pain_score": vitals.pain_score,
    }


def _serialize_visit(visit):
    try:
        queue = visit.queue
    except Queue.DoesNotExist:
        queue = None
    try:
        assessment = visit.opd_assessment
    except ObjectDoesNotExist:
        assessment = None
    status_label = (
        PUBLIC_STATUS.get(queue.status, (queue.get_status_display(), ""))[0]
        if queue else "ไม่พบข้อมูลคิว"
    )
    return {
        "queue_number": _queue_number(queue) if queue else None,
        "registered_at": visit.registered_at.isoformat(),
        "note": visit.note or "",
        "status": queue.status if queue else None,
        "status_label": status_label,
        "room": f"ห้องตรวจ {queue.exam_room}" if queue and queue.exam_room else None,
        "vitals": _serialize_vitals(visit),
        "diagnosis": assessment.diagnosis if assessment else "",
        "treatment": assessment.treatment if assessment else "",
    }


def _people_ahead(queue):
    if queue.status not in {Queue.Status.WAITING_QUEUE, Queue.Status.WAITING}:
        return 0
    return Queue.objects.filter(
        status__in=[Queue.Status.WAITING_QUEUE, Queue.Status.WAITING],
    ).exclude(pk=queue.pk).filter(
        Q(priority__lt=queue.priority)
        | Q(priority=queue.priority, created_at__lt=queue.created_at)
    ).count()


def _queue_position(queue):
    if queue.status not in {Queue.Status.WAITING_QUEUE, Queue.Status.WAITING}:
        return None
    return _people_ahead(queue) + 1


@csrf_exempt
def public_register(request):
    if request.method == "OPTIONS":
        return _cors_json(request, {})
    if request.method != "POST":
        return _cors_json(request, {"ok": False, "error": "Method not allowed"}, status=405)

    if rate_limited(request, "patient-register", limit=30, window_seconds=300):
        return _cors_json(request, {"ok": False, "error": "ส่งข้อมูลบ่อยเกินไป กรุณารอสักครู่"}, status=429)

    payload, error, error_status = _json_body(request)
    if error:
        return _cors_json(request, {"ok": False, "error": error}, status=error_status)
    if payload.get("website"):
        return _cors_json(request, {"ok": True}, status=202)

    username = str(payload.get("username") or "").strip() or None
    password = str(payload.get("password") or "")
    email = _normalize_email(payload.get("email")) or None
    temp_token = str(payload.get("temp_token") or payload.get("google_temp_token") or "").strip()
    google_claims = None

    field_errors = {}
    if username and not USERNAME_PATTERN.fullmatch(username):
        field_errors["username"] = ["ชื่อผู้ใช้ต้องยาว 3-50 ตัว และใช้ได้เฉพาะ a-z, A-Z, 0-9, จุด, ขีดกลาง หรือขีดล่าง"]
    if password:
        password_error = _validate_portal_password(password)
        if password_error:
            field_errors["password"] = [password_error]
    elif username:
        field_errors["password"] = ["กรุณาระบุรหัสผ่านสำหรับบัญชีนี้"]

    if temp_token:
        try:
            google_claims = signing.loads(
                temp_token,
                salt=GOOGLE_LINK_SALT,
                max_age=int(getattr(settings, "GOOGLE_LINK_TOKEN_MAX_AGE", 600)),
            )
        except signing.BadSignature:
            field_errors["temp_token"] = ["ข้อมูลเชื่อมบัญชี Google ไม่ถูกต้องหรือหมดอายุ"]
        if google_claims and google_claims.get("email"):
            email = _normalize_email(google_claims["email"])

    if (username or password or temp_token) and not email:
        field_errors["email"] = ["กรุณาระบุอีเมลสำหรับบัญชีผู้ป่วย"]

    form_payload = dict(payload)
    if email is not None:
        form_payload["email"] = email
    form = PublicPatientRegistrationForm(form_payload)
    if not form.is_valid():
        for field, messages_ in form.errors.items():
            field_errors.setdefault(field, []).extend(str(message) for message in messages_)
    if field_errors:
        return _cors_json(request, {"ok": False, "error": "กรุณาตรวจสอบข้อมูลที่กรอก", "errors": field_errors}, status=400)

    national_id = form.cleaned_data["national_id"]
    existing = Patient.objects.filter(national_id=national_id).first()

    if username:
        username_owner = Patient.objects.filter(username__iexact=username).exclude(pk=getattr(existing, "pk", None)).first()
        if username_owner:
            return _cors_json(
                request,
                {"ok": False, "error": "ชื่อผู้ใช้นี้ถูกใช้งานแล้ว", "errors": {"username": ["ชื่อผู้ใช้นี้ถูกใช้งานแล้ว"]}},
                status=409,
            )
    if email:
        email_owner = Patient.objects.filter(email__iexact=email).exclude(pk=getattr(existing, "pk", None)).first()
        if email_owner:
            return _cors_json(
                request,
                {"ok": False, "error": "อีเมลนี้ถูกใช้กับบัญชีอื่นแล้ว", "errors": {"email": ["อีเมลนี้ถูกใช้กับบัญชีอื่นแล้ว"]}},
                status=409,
            )

    if google_claims and google_claims.get("sub"):
        google_owner = Patient.objects.filter(
            google_id=str(google_claims["sub"])
        ).exclude(pk=getattr(existing, "pk", None)).first()
        if google_owner:
            return _cors_json(
                request,
                {"ok": False, "error": "บัญชี Google นี้เชื่อมกับผู้ป่วยรายอื่นแล้ว"},
                status=409,
            )

    contacts, contacts_error = _validate_emergency_contacts(payload.get("emergency_contacts"))
    if contacts_error:
        return _cors_json(
            request,
            {"ok": False, "error": contacts_error, "errors": {"emergency_contacts": [contacts_error]}},
            status=400,
        )

    with transaction.atomic():
        patient, created = Patient.objects.select_for_update().get_or_create(
            national_id=national_id,
            defaults={key: value for key, value in form.cleaned_data.items() if key != "consent"},
        )
        active_visit = (
            Visit.objects.select_related("queue")
            .filter(patient=patient, queue__status__in=ACTIVE_QUEUE_STATUSES)
            .order_by("-registered_at")
            .first()
        )
        if active_visit:
            return _cors_json(
                request,
                {
                    "ok": False,
                    "error": "คุณมีคิวที่กำลังรับบริการอยู่แล้ว กรุณาตรวจสอบคิวเดิมก่อนจองใหม่",
                    "active_queue": _serialize_queue(active_visit.queue),
                },
                status=409,
            )

        if not created:
            for field, value in form.cleaned_data.items():
                if field == "consent":
                    continue
                if field == "birth_date" and "birth_date" not in payload:
                    continue
                setattr(patient, field, value)

        patient.email = email
        if username:
            patient.username = username
        if password:
            patient.password_hash = make_password(password)
        if contacts is not None:
            patient.emergency_contacts = contacts
            first_contact = contacts[0] if contacts else {}
            patient.emergency_name = first_contact.get("name", "")
            patient.emergency_relationship = first_contact.get("relationship", "")
            patient.emergency_phone = first_contact.get("phone", "")
        elif patient.emergency_name or patient.emergency_phone:
            patient.emergency_contacts = [{
                "id": "primary",
                "name": patient.emergency_name,
                "relationship": patient.emergency_relationship,
                "phone": patient.emergency_phone,
            }]
        if google_claims:
            patient.google_id = str(google_claims.get("sub") or "") or None
            patient.email_verified = bool(google_claims.get("email_verified", True))
        patient.save()

        visit = Visit.objects.create(patient=patient, note=patient.note)
        VitalSign.objects.create(visit=visit)
        Queue.objects.create(visit=visit, status=Queue.Status.WAITING_VITALS)

    access_token, expires_at = _issue_patient_token(patient)
    return _cors_json(request, {
        "ok": True,
        "tracking_token": str(visit.tracking_token),
        "access_token": access_token,
        "token_type": "Bearer",
        "expires_in": int(getattr(settings, "PATIENT_TOKEN_MAX_AGE", 60 * 60 * 12)),
        "expires_at": expires_at.isoformat(),
        "patient_id": patient.pk,
        "hn": patient.hn,
        "status_url": f"/api/patient/queue/{visit.tracking_token}/",
        "message": "ลงทะเบียนและรับบัตรคิวสำเร็จ",
        **_serialize_queue(visit.queue),
    }, status=201)


@csrf_exempt
def public_queue_status(request, tracking_token):
    if request.method == "OPTIONS":
        return _cors_json(request, {})
    if request.method != "GET":
        return _cors_json(request, {"ok": False, "error": "Method not allowed"}, status=405)

    try:
        visit = Visit.objects.select_related("queue").get(tracking_token=tracking_token)
    except Visit.DoesNotExist:
        return _cors_json(request, {"ok": False, "error": "ไม่พบข้อมูลคิว"}, status=404)
    queue = visit.queue
    label, instruction = PUBLIC_STATUS.get(queue.status, ("กำลังตรวจสอบสถานะ", "กรุณาติดต่อเจ้าหน้าที่"))
    return _cors_json(request, {
        "ok": True,
        "queue_number": _queue_number(queue),
        "status": queue.status,
        "status_label": label,
        "instruction": instruction,
        "people_ahead": _people_ahead(queue),
        "queue_position": _queue_position(queue),
        "room": f"ห้องตรวจ {queue.exam_room}" if queue.exam_room else None,
        "updated_at": timezone.now().isoformat(),
    })


@csrf_exempt
def patient_login(request):
    if request.method == "OPTIONS":
        return _cors_json(request, {})
    if request.method != "POST":
        return _cors_json(request, {"ok": False, "error": "Method not allowed"}, status=405)

    payload, error, error_status = _json_body(request)
    if error:
        return _cors_json(request, {"ok": False, "error": error}, status=error_status)

    identifier = str(payload.get("identifier") or payload.get("national_id") or "").strip()
    password = payload.get("password")
    has_password = password is not None and str(password) != ""

    if rate_limited_by_identifier(
        request,
        "patient-login",
        identifier.lower() or "missing",
        limit=int(getattr(settings, "PATIENT_LOGIN_RATE_LIMIT", 5)),
        window_seconds=int(getattr(settings, "PATIENT_LOGIN_RATE_WINDOW", 60)),
    ):
        return _cors_json(
            request,
            {"ok": False, "error": "พยายามเข้าสู่ระบบบ่อยเกินไป กรุณารอสักครู่"},
            status=429,
        )

    if not identifier:
        return _cors_json(request, {"ok": False, "error": "กรุณาระบุข้อมูลเข้าสู่ระบบ"}, status=400)

    patient = _lookup_patient_by_identifier(identifier)
    if not patient or not patient.is_active:
        return _cors_json(
            request,
            {"ok": False, "error": "ชื่อผู้ใช้งานหรือรหัสผ่านไม่ถูกต้อง"},
            status=401,
        )

    if has_password:
        # Google-only / legacy records can have no password. Never accept a
        # password login when there is no server-side password hash.
        if not patient.password_hash or not check_password(str(password), patient.password_hash):
            return _cors_json(
                request,
                {"ok": False, "error": "ชื่อผู้ใช้งานหรือรหัสผ่านไม่ถูกต้อง"},
                status=401,
            )
    else:
        # Backward compatibility for the existing patient portal: legacy
        # national-ID-only login remains available only when the caller
        # explicitly supplies national_id and it is a valid 13-digit value.
        national_id = str(payload.get("national_id") or "").strip()
        if not re.fullmatch(r"[0-9]{13}", national_id) or national_id != patient.national_id:
            return _cors_json(
                request,
                {"ok": False, "error": "ชื่อผู้ใช้งานหรือรหัสผ่านไม่ถูกต้อง"},
                status=401,
            )

    access_token, expires_at = _issue_patient_token(patient)
    return _cors_json(request, {
        "ok": True,
        "access_token": access_token,
        "token_type": "Bearer",
        "expires_in": int(getattr(settings, "PATIENT_TOKEN_MAX_AGE", 60 * 60 * 12)),
        "expires_at": expires_at.isoformat(),
        "profile": _patient_profile_payload(patient, mask_national_id=False),
        "message": "เข้าสู่ระบบสำเร็จ",
    })


@csrf_exempt
def patient_google_auth(request):
    if request.method == "OPTIONS":
        return _cors_json(request, {})
    if request.method != "POST":
        return _cors_json(request, {"ok": False, "error": "Method not allowed"}, status=405)
    if rate_limited(request, "patient-google-login", limit=10, window_seconds=300):
        return _cors_json(request, {"ok": False, "error": "พยายามเข้าสู่ระบบบ่อยเกินไป กรุณารอสักครู่"}, status=429)

    payload, error, error_status = _json_body(request)
    if error:
        return _cors_json(request, {"ok": False, "error": error}, status=error_status)
    credential = str(payload.get("credential") or "").strip()
    google_access_token = str(payload.get("access_token") or "").strip()
    if not credential and not google_access_token:
        return _cors_json(request, {"ok": False, "error": "ไม่พบข้อมูลยืนยันจาก Google"}, status=400)

    try:
        claims = (
            _verify_google_access_token(google_access_token)
            if google_access_token
            else _verify_google_credential(credential)
        )
    except RuntimeError:
        return _cors_json(request, {"ok": False, "error": "ระบบ Google Sign-In ยังไม่ได้ตั้งค่า"}, status=503)
    except Exception:
        security_logger.warning("patient_google_token_rejected")
        return _cors_json(request, {"ok": False, "error": "ไม่สามารถยืนยันบัญชี Google ได้"}, status=401)

    email = _normalize_email(claims.get("email"))
    google_id = str(claims.get("sub") or "").strip()
    if not google_id or not email or not bool(claims.get("email_verified")):
        return _cors_json(request, {"ok": False, "error": "บัญชี Google ต้องมีอีเมลที่ยืนยันแล้ว"}, status=401)

    patient = Patient.objects.filter(google_id=google_id, is_active=True).first()
    if patient:
        access_token, expires_at = _issue_patient_token(patient)
        return _cors_json(request, {
            "ok": True,
            "access_token": access_token,
            "token_type": "Bearer",
            "expires_in": int(getattr(settings, "PATIENT_TOKEN_MAX_AGE", 60 * 60 * 12)),
            "expires_at": expires_at.isoformat(),
            "profile": _patient_profile_payload(patient, mask_national_id=False),
            "message": "เข้าสู่ระบบด้วย Google สำเร็จ",
        })

    email_matches = Patient.objects.filter(email__iexact=email, is_active=True)
    if email_matches.count() > 1:
        return _cors_json(
            request,
            {"ok": False, "error": "พบอีเมลซ้ำในระบบ กรุณาติดต่อเจ้าหน้าที่เพื่อยืนยันบัญชี"},
            status=409,
        )
    patient = email_matches.first()
    if patient:
        if patient.google_id and patient.google_id != google_id:
            return _cors_json(
                request,
                {"ok": False, "error": "อีเมลนี้เชื่อมกับบัญชี Google อื่นแล้ว กรุณาติดต่อเจ้าหน้าที่"},
                status=409,
            )
        patient.google_id = google_id
        patient.email = email
        patient.email_verified = True
        patient.save(update_fields=["google_id", "email", "email_verified", "updated_at"])
        access_token, expires_at = _issue_patient_token(patient)
        return _cors_json(request, {
            "ok": True,
            "access_token": access_token,
            "token_type": "Bearer",
            "expires_in": int(getattr(settings, "PATIENT_TOKEN_MAX_AGE", 60 * 60 * 12)),
            "expires_at": expires_at.isoformat(),
            "profile": _patient_profile_payload(patient, mask_national_id=False),
            "message": "เชื่อมบัญชี Google สำเร็จ",
        })

    full_name = str(claims.get("name") or "").strip().split()
    first_name = str(claims.get("given_name") or (full_name[0] if full_name else "")).strip()
    last_name = str(claims.get("family_name") or (" ".join(full_name[1:]) if len(full_name) > 1 else "")).strip()
    temp_token = signing.dumps(
        {
            "sub": google_id,
            "email": email,
            "email_verified": True,
            "first_name": first_name,
            "last_name": last_name,
        },
        salt=GOOGLE_LINK_SALT,
    )
    return _cors_json(request, {
        "ok": True,
        "is_new_user": True,
        "temp_token": temp_token,
        "suggested_profile": {
            "email": email,
            "first_name": first_name,
            "last_name": last_name,
        },
    })


@csrf_exempt
def patient_password_reset_request(request):
    if request.method == "OPTIONS":
        return _cors_json(request, {})
    if request.method != "POST":
        return _cors_json(request, {"ok": False, "error": "Method not allowed"}, status=405)

    payload, error, error_status = _json_body(request)
    if error:
        return _cors_json(request, {"ok": False, "error": error}, status=error_status)
    identifier = str(payload.get("identifier") or "").strip()
    channel = str(payload.get("channel") or "email").strip().lower()

    generic = {
        "ok": True,
        "message": "ส่งรหัส OTP เรียบร้อยแล้ว หากมีบัญชีในระบบ",
        "cooldown_seconds": 60,
        "expires_in_seconds": int(getattr(settings, "OTP_TTL_SECONDS", 300)),
        "masked_target": None,
    }

    if not identifier:
        return _cors_json(request, generic)
    if channel in {"sms", "phone"}:
        return _cors_json(request, {"ok": False, "error": "ยังไม่เปิดให้บริการรับรหัสทาง SMS กรุณาใช้อีเมล"}, status=400)
    if channel != "email":
        return _cors_json(request, {"ok": False, "error": "ช่องทางรับรหัสไม่ถูกต้อง"}, status=400)

    if rate_limited_by_identifier(
        request,
        "patient-password-reset",
        identifier.lower(),
        limit=int(getattr(settings, "OTP_REQUEST_LIMIT", 3)),
        window_seconds=int(getattr(settings, "OTP_REQUEST_WINDOW", 900)),
    ):
        return _cors_json(request, {"ok": False, "error": "ขอรหัสถี่เกินไป กรุณารอสักครู่"}, status=429)

    patient = _lookup_patient_by_identifier(identifier)
    if not patient or not patient.is_active or not patient.email:
        return _cors_json(request, generic)

    generic["masked_target"] = _mask_email(patient.email)
    otp = f"{secrets.randbelow(1_000_000):06d}"
    now = timezone.now()
    with transaction.atomic():
        Patient.objects.select_for_update().only("pk").get(pk=patient.pk)
        OtpChallenge.objects.filter(
            national_id=patient.national_id,
            purpose=OtpChallenge.Purpose.PASSWORD_RESET,
            consumed_at__isnull=True,
        ).update(consumed_at=now)
        challenge = OtpChallenge.objects.create(
            national_id=patient.national_id,
            channel=OtpChallenge.Channel.EMAIL,
            purpose=OtpChallenge.Purpose.PASSWORD_RESET,
            code_hash=make_password(otp),
            target=patient.email,
            expires_at=now + timedelta(seconds=int(getattr(settings, "OTP_TTL_SECONDS", 300))),
        )

    try:
        send_mail(
            "รหัสยืนยัน (OTP) สำหรับเปลี่ยนรหัสผ่าน - โรงพยาบาล",
            (
                f"รหัสยืนยันของคุณคือ  {otp}\n"
                "รหัสนี้ใช้ได้ภายใน 5 นาที และใช้ได้ครั้งเดียว\n"
                "หากคุณไม่ได้เป็นผู้ขอ กรุณาละเว้นอีเมลฉบับนี้"
            ),
            settings.DEFAULT_FROM_EMAIL,
            [patient.email],
            fail_silently=False,
        )
    except Exception:
        OtpChallenge.objects.filter(pk=challenge.pk).update(consumed_at=timezone.now())
        security_logger.exception("patient_password_reset_email_delivery_failed")
        return _cors_json(
            request,
            {
                "ok": False,
                "error": "ไม่สามารถส่งอีเมล OTP ได้ กรุณาติดต่อเจ้าหน้าที่หรือลองใหม่ภายหลัง",
            },
            status=503,
        )
    return _cors_json(request, generic)


@csrf_exempt
def patient_password_reset_verify_otp(request):
    if request.method == "OPTIONS":
        return _cors_json(request, {})
    if request.method != "POST":
        return _cors_json(request, {"ok": False, "error": "Method not allowed"}, status=405)

    payload, error, error_status = _json_body(request)
    if error:
        return _cors_json(request, {"ok": False, "error": error}, status=error_status)
    identifier = str(payload.get("identifier") or "").strip()
    otp = str(payload.get("otp") or "").strip()
    patient = _lookup_patient_by_identifier(identifier)
    if not patient or not patient.is_active:
        return _cors_json(request, {"ok": False, "error": "รหัส OTP ไม่ถูกต้องหรือหมดอายุ"}, status=400)

    with transaction.atomic():
        challenge = (
            OtpChallenge.objects.select_for_update()
            .filter(
                national_id=patient.national_id,
                purpose=OtpChallenge.Purpose.PASSWORD_RESET,
                consumed_at__isnull=True,
            )
            .order_by("-created_at")
            .first()
        )
        if not challenge:
            return _cors_json(request, {"ok": False, "error": "รหัส OTP ไม่ถูกต้องหรือหมดอายุ"}, status=400)
        if challenge.expires_at <= timezone.now():
            return _cors_json(request, {"ok": False, "error": "รหัส OTP หมดอายุ กรุณาขอใหม่"}, status=400)

        max_attempts = int(getattr(settings, "OTP_MAX_ATTEMPTS", 5))
        if challenge.attempts >= max_attempts:
            return _cors_json(request, {"ok": False, "error": "กรอกรหัส OTP ผิดเกินกำหนด กรุณาขอใหม่"}, status=400)
        if not re.fullmatch(r"[0-9]{6}", otp) or not check_password(otp, challenge.code_hash):
            challenge.attempts += 1
            challenge.save(update_fields=["attempts"])
            message = "กรอกรหัส OTP ผิดเกินกำหนด กรุณาขอใหม่" if challenge.attempts >= max_attempts else "รหัส OTP ไม่ถูกต้อง"
            return _cors_json(request, {"ok": False, "error": message}, status=400)

        reset_token = secrets.token_hex(32)
        challenge.reset_token_hash = _token_digest(reset_token)
        challenge.reset_token_expires_at = timezone.now() + timedelta(
            seconds=int(getattr(settings, "PASSWORD_RESET_TOKEN_TTL_SECONDS", 900))
        )
        # Rotate the OTP hash so the same OTP cannot mint another reset token.
        challenge.code_hash = make_password(secrets.token_urlsafe(24))
        challenge.save(update_fields=["reset_token_hash", "reset_token_expires_at", "code_hash"])

    return _cors_json(request, {
        "ok": True,
        "reset_token": reset_token,
        "message": "รหัส OTP ถูกต้อง กรุณาตั้งรหัสผ่านใหม่",
    })


@csrf_exempt
def patient_password_reset_confirm(request):
    if request.method == "OPTIONS":
        return _cors_json(request, {})
    if request.method != "POST":
        return _cors_json(request, {"ok": False, "error": "Method not allowed"}, status=405)

    payload, error, error_status = _json_body(request)
    if error:
        return _cors_json(request, {"ok": False, "error": error}, status=error_status)
    reset_token = str(payload.get("reset_token") or "").strip()
    new_password = str(payload.get("new_password") or "")
    confirm_password = str(payload.get("confirm_password") or "")
    identifier = str(payload.get("identifier") or "").strip()

    if not reset_token:
        return _cors_json(request, {"ok": False, "error": "Reset token ไม่ถูกต้องหรือหมดอายุ"}, status=400)
    if confirm_password and new_password != confirm_password:
        return _cors_json(
            request,
            {"ok": False, "error": "รหัสผ่านยืนยันไม่ตรงกัน", "errors": {"confirm_password": ["รหัสผ่านยืนยันไม่ตรงกัน"]}},
            status=400,
        )
    password_error = _validate_portal_password(new_password)
    if password_error:
        return _cors_json(
            request,
            {"ok": False, "error": password_error, "errors": {"new_password": [password_error]}},
            status=400,
        )

    with transaction.atomic():
        challenge = (
            OtpChallenge.objects.select_for_update()
            .filter(
                purpose=OtpChallenge.Purpose.PASSWORD_RESET,
                reset_token_hash=_token_digest(reset_token),
                consumed_at__isnull=True,
            )
            .first()
        )
        if (
            not challenge
            or not challenge.reset_token_expires_at
            or challenge.reset_token_expires_at <= timezone.now()
        ):
            return _cors_json(request, {"ok": False, "error": "Reset token ไม่ถูกต้องหรือหมดอายุ"}, status=400)

        patient = Patient.objects.select_for_update().filter(national_id=challenge.national_id).first()
        if not patient or not patient.is_active:
            return _cors_json(request, {"ok": False, "error": "Reset token ไม่ถูกต้องหรือหมดอายุ"}, status=400)
        if identifier:
            owner = _lookup_patient_by_identifier(identifier)
            if not owner or owner.pk != patient.pk:
                return _cors_json(request, {"ok": False, "error": "Reset token ไม่ตรงกับบัญชีผู้ใช้"}, status=400)

        patient.password_hash = make_password(new_password)
        patient.token_version += 1
        patient.save(update_fields=["password_hash", "token_version", "updated_at"])
        PatientAccessToken.objects.filter(patient=patient).delete()
        challenge.consumed_at = timezone.now()
        challenge.reset_token_hash = None
        challenge.save(update_fields=["consumed_at", "reset_token_hash"])

    if patient.email:
        try:
            send_mail(
                "แจ้งเตือนการเปลี่ยนรหัสผ่าน - โรงพยาบาล",
                "รหัสผ่านสำหรับบัญชีผู้ป่วยของคุณถูกเปลี่ยนแล้ว หากไม่ใช่คุณ กรุณาติดต่อโรงพยาบาลทันที",
                settings.DEFAULT_FROM_EMAIL,
                [patient.email],
                fail_silently=False,
            )
        except Exception:
            security_logger.exception("patient_password_reset_notification_failed")

    return _cors_json(request, {"ok": True, "message": "เปลี่ยนรหัสผ่านสำเร็จ กรุณาเข้าสู่ระบบใหม่"})


@csrf_exempt
def patient_pin_setup(request):
    if request.method == "OPTIONS":
        return _cors_json(request, {})
    if request.method != "POST":
        return _cors_json(request, {"ok": False, "error": "Method not allowed"}, status=405)
    patient = _authenticated_patient(request)
    if not patient:
        return _cors_json(
            request,
            {"ok": False, "error": "โทเคนไม่ถูกต้องหรือหมดอายุ กรุณาเข้าสู่ระบบใหม่"},
            status=401,
        )
    payload, error, error_status = _json_body(request)
    if error:
        return _cors_json(request, {"ok": False, "error": error}, status=error_status)
    pin = str(payload.get("pin") or "")
    if not PIN_PATTERN.fullmatch(pin):
        return _cors_json(
            request,
            {"ok": False, "error": "รหัส PIN ต้องเป็นตัวเลข 6 หลัก"},
            status=400,
        )
    with transaction.atomic():
        pin_state, _ = PatientPin.objects.select_for_update().get_or_create(
            patient=patient,
            defaults={"pin_hash": make_password(pin)},
        )
        _reset_pin_state(pin_state, pin)
    access_token, _ = _issue_patient_token(patient)
    return _cors_json(
        request,
        {"ok": True, "access_token": access_token, "message": "ตั้งรหัส PIN สำเร็จ"},
    )


@csrf_exempt
def patient_pin_verify(request):
    if request.method == "OPTIONS":
        return _cors_json(request, {})
    if request.method != "POST":
        return _cors_json(request, {"ok": False, "error": "Method not allowed"}, status=405)
    payload, error, error_status = _json_body(request)
    if error:
        return _cors_json(request, {"ok": False, "error": error}, status=error_status)
    national_id = str(payload.get("national_id") or "").strip()
    pin = str(payload.get("pin") or "")
    if not re.fullmatch(r"[0-9]{13}", national_id) or not PIN_PATTERN.fullmatch(pin):
        return _cors_json(request, {"ok": False, "error": "ข้อมูลไม่ถูกต้อง"}, status=400)
    if rate_limited_by_identifier(
        request,
        "patient-pin-verify",
        national_id,
        limit=int(getattr(settings, "PIN_VERIFY_RATE_LIMIT", 30)),
        window_seconds=int(getattr(settings, "PIN_VERIFY_RATE_WINDOW", 300)),
    ):
        return _cors_json(
            request,
            {"ok": False, "error": "พยายามตรวจรหัสถี่เกินไป กรุณารอสักครู่"},
            status=429,
        )

    with transaction.atomic():
        pin_state = (
            PatientPin.objects.select_for_update()
            .select_related("patient")
            .filter(patient__national_id=national_id)
            .first()
        )
        if not pin_state:
            return _cors_json(
                request,
                {"ok": False, "error": "ยังไม่ได้ตั้งรหัส PIN"},
                status=404,
            )
        if _pin_is_locked(pin_state):
            return _locked_response(request, pin_state)
        if not check_password(pin, pin_state.pin_hash):
            attempts_left, locked_until = _record_pin_failure(pin_state)
            payload = {"ok": False, "error": "รหัส PIN ไม่ถูกต้อง", "attempts_left": attempts_left}
            if locked_until:
                payload["locked_until"] = locked_until.isoformat()
            return _cors_json(request, payload, status=401)
        _reset_pin_state(pin_state)
        patient = pin_state.patient

    access_token, _ = _issue_patient_token(patient)
    return _cors_json(request, {"ok": True, "access_token": access_token})


@csrf_exempt
def patient_pin_change(request):
    if request.method == "OPTIONS":
        return _cors_json(request, {})
    if request.method != "POST":
        return _cors_json(request, {"ok": False, "error": "Method not allowed"}, status=405)
    patient = _authenticated_patient(request)
    if not patient:
        return _cors_json(
            request,
            {"ok": False, "error": "โทเคนไม่ถูกต้องหรือหมดอายุ กรุณาเข้าสู่ระบบใหม่"},
            status=401,
        )
    payload, error, error_status = _json_body(request)
    if error:
        return _cors_json(request, {"ok": False, "error": error}, status=error_status)
    current_pin = str(payload.get("current_pin") or "")
    new_pin = str(payload.get("new_pin") or "")
    if not PIN_PATTERN.fullmatch(current_pin) or not PIN_PATTERN.fullmatch(new_pin):
        return _cors_json(
            request,
            {"ok": False, "error": "รหัส PIN ต้องเป็นตัวเลข 6 หลัก"},
            status=400,
        )

    with transaction.atomic():
        pin_state = PatientPin.objects.select_for_update().filter(patient=patient).first()
        if not pin_state:
            return _cors_json(
                request,
                {"ok": False, "error": "ยังไม่ได้ตั้งรหัส PIN"},
                status=404,
            )
        if _pin_is_locked(pin_state):
            return _locked_response(request, pin_state)
        if not check_password(current_pin, pin_state.pin_hash):
            attempts_left, locked_until = _record_pin_failure(pin_state)
            response_payload = {
                "ok": False,
                "error": "รหัส PIN เดิมไม่ถูกต้อง",
                "attempts_left": attempts_left,
            }
            if locked_until:
                response_payload["locked_until"] = locked_until.isoformat()
            return _cors_json(request, response_payload, status=401)
        if new_pin == current_pin:
            return _cors_json(
                request,
                {"ok": False, "error": "รหัส PIN ใหม่ต้องไม่ซ้ำกับรหัสเดิม"},
                status=400,
            )
        _reset_pin_state(pin_state, new_pin)

    if patient.email:
        try:
            send_mail(
                "แจ้งเตือนการเปลี่ยนรหัส PIN - โรงพยาบาล",
                "รหัส PIN สำหรับบัญชีผู้ป่วยของคุณถูกเปลี่ยนแล้ว หากไม่ใช่คุณ กรุณาติดต่อโรงพยาบาลทันที",
                settings.DEFAULT_FROM_EMAIL,
                [patient.email],
                fail_silently=False,
            )
        except Exception:
            security_logger.exception("patient_pin_change_notification_failed")
    return _cors_json(request, {"ok": True, "message": "เปลี่ยนรหัส PIN สำเร็จ"})


@csrf_exempt
def patient_pin_reset_request(request):
    if request.method == "OPTIONS":
        return _cors_json(request, {})
    if request.method != "POST":
        return _cors_json(request, {"ok": False, "error": "Method not allowed"}, status=405)
    payload, error, error_status = _json_body(request)
    if error:
        return _cors_json(request, {"ok": False, "error": error}, status=error_status)
    national_id = str(payload.get("national_id") or "").strip()
    channel = str(payload.get("channel") or "").strip().lower()
    if not re.fullmatch(r"[0-9]{13}", national_id):
        return _cors_json(request, {"ok": False, "error": "ข้อมูลไม่ถูกต้อง"}, status=400)
    if channel not in {OtpChallenge.Channel.EMAIL, OtpChallenge.Channel.PHONE}:
        return _cors_json(request, {"ok": False, "error": "ช่องทางรับรหัสไม่ถูกต้อง"}, status=400)
    if rate_limited_by_identifier(
        request,
        "patient-pin-reset",
        national_id,
        limit=int(getattr(settings, "OTP_REQUEST_LIMIT", 3)),
        window_seconds=int(getattr(settings, "OTP_REQUEST_WINDOW", 900)),
    ):
        return _cors_json(
            request,
            {"ok": False, "error": "ขอรหัสถี่เกินไป กรุณารอสักครู่"},
            status=429,
        )
    if channel == OtpChallenge.Channel.PHONE:
        return _cors_json(
            request,
            {"ok": False, "error": "ยังไม่เปิดให้บริการรับรหัสทาง SMS กรุณาใช้อีเมล"},
            status=400,
        )

    patient = Patient.objects.filter(national_id=national_id).only("email").first()
    generic_response = {
        "ok": True,
        "message": "ส่งรหัส OTP เรียบร้อยแล้ว",
        "resend_after_seconds": 60,
        "cooldown_seconds": 60,
        "expires_in_seconds": int(getattr(settings, "OTP_TTL_SECONDS", 300)),
        "masked_target": None,
    }
    if not patient or not patient.email:
        return _cors_json(request, generic_response)

    generic_response["masked_target"] = _mask_email(patient.email)
    otp = f"{secrets.randbelow(1_000_000):06d}"
    now = timezone.now()
    with transaction.atomic():
        # Serialize reset requests for this patient so concurrent requests cannot
        # leave more than one active challenge.
        Patient.objects.select_for_update().only("pk").get(pk=patient.pk)
        OtpChallenge.objects.filter(
            national_id=national_id,
            channel=channel,
            purpose=OtpChallenge.Purpose.PIN_RESET,
            consumed_at__isnull=True,
        ).update(consumed_at=now)
        challenge = OtpChallenge.objects.create(
            national_id=national_id,
            channel=channel,
            purpose=OtpChallenge.Purpose.PIN_RESET,
            code_hash=make_password(otp),
            target=patient.email,
            expires_at=now + timedelta(seconds=int(getattr(settings, "OTP_TTL_SECONDS", 300))),
        )

    try:
        send_mail(
            "รหัสยืนยัน (OTP) สำหรับตั้งรหัส PIN ใหม่ - โรงพยาบาล",
            (
                f"รหัสยืนยันของคุณคือ  {otp}\n"
                "รหัสนี้ใช้ได้ภายใน 5 นาที และใช้ได้ครั้งเดียว\n"
                "หากคุณไม่ได้เป็นผู้ขอ กรุณาละเว้นอีเมลฉบับนี้"
            ),
            settings.DEFAULT_FROM_EMAIL,
            [patient.email],
            fail_silently=False,
        )
    except Exception:
        OtpChallenge.objects.filter(pk=challenge.pk).update(consumed_at=timezone.now())
        security_logger.exception("patient_pin_reset_email_delivery_failed")
        return _cors_json(
            request,
            {
                "ok": False,
                "error": "ไม่สามารถส่งอีเมล OTP ได้ กรุณาติดต่อเจ้าหน้าที่หรือลองใหม่ภายหลัง",
            },
            status=503,
        )
    return _cors_json(request, generic_response)


@csrf_exempt
def patient_pin_reset_confirm(request):
    if request.method == "OPTIONS":
        return _cors_json(request, {})
    if request.method != "POST":
        return _cors_json(request, {"ok": False, "error": "Method not allowed"}, status=405)
    payload, error, error_status = _json_body(request)
    if error:
        return _cors_json(request, {"ok": False, "error": error}, status=error_status)
    national_id = str(payload.get("national_id") or "").strip()
    otp = str(payload.get("otp") or "")
    pin = str(payload.get("pin") or payload.get("new_pin") or "")
    if not re.fullmatch(r"[0-9]{13}", national_id):
        return _cors_json(request, {"ok": False, "error": "ข้อมูลไม่ถูกต้อง"}, status=400)
    if not PIN_PATTERN.fullmatch(pin):
        return _cors_json(
            request,
            {"ok": False, "error": "รหัส PIN ต้องเป็นตัวเลข 6 หลัก"},
            status=400,
        )

    with transaction.atomic():
        challenge = (
            OtpChallenge.objects.select_for_update()
            .filter(
                national_id=national_id,
                purpose=OtpChallenge.Purpose.PIN_RESET,
                consumed_at__isnull=True,
            )
            .order_by("-created_at")
            .first()
        )
        if not challenge:
            return _cors_json(
                request,
                {"ok": False, "error": "ไม่พบรหัส OTP ที่ใช้งานได้ กรุณาขอใหม่"},
                status=400,
            )
        if challenge.expires_at <= timezone.now():
            return _cors_json(
                request,
                {"ok": False, "error": "รหัส OTP หมดอายุ กรุณาขอใหม่"},
                status=400,
            )
        max_attempts = int(getattr(settings, "OTP_MAX_ATTEMPTS", 5))
        if challenge.attempts >= max_attempts:
            return _cors_json(
                request,
                {"ok": False, "error": "กรอกรหัส OTP ผิดเกินกำหนด กรุณาขอใหม่"},
                status=400,
            )
        if not re.fullmatch(r"[0-9]{6}", otp) or not check_password(otp, challenge.code_hash):
            challenge.attempts += 1
            challenge.save(update_fields=["attempts"])
            message = (
                "กรอกรหัส OTP ผิดเกินกำหนด กรุณาขอใหม่"
                if challenge.attempts >= max_attempts
                else "รหัส OTP ไม่ถูกต้อง"
            )
            return _cors_json(request, {"ok": False, "error": message}, status=400)

        patient = Patient.objects.filter(national_id=national_id).first()
        if not patient:
            return _cors_json(
                request,
                {"ok": False, "error": "ไม่สามารถยืนยันรหัส OTP ได้ กรุณาขอใหม่"},
                status=400,
            )
        challenge.consumed_at = timezone.now()
        challenge.save(update_fields=["consumed_at"])
        pin_state, _ = PatientPin.objects.select_for_update().get_or_create(
            patient=patient,
            defaults={"pin_hash": make_password(pin)},
        )
        _reset_pin_state(pin_state, pin)

    access_token, _ = _issue_patient_token(patient)
    if patient.email:
        try:
            send_mail(
                "แจ้งเตือนการตั้งรหัส PIN ใหม่ - โรงพยาบาล",
                "รหัส PIN สำหรับบัญชีผู้ป่วยของคุณถูกตั้งใหม่เรียบร้อยแล้ว หากไม่ใช่คุณ กรุณาติดต่อโรงพยาบาลทันที",
                settings.DEFAULT_FROM_EMAIL,
                [patient.email],
                fail_silently=False,
            )
        except Exception:
            security_logger.exception("patient_pin_reset_notification_failed")
    return _cors_json(
        request,
        {"ok": True, "access_token": access_token, "message": "ตั้งรหัส PIN ใหม่สำเร็จ"},
    )


@csrf_exempt
def patient_me(request):
    if request.method == "OPTIONS":
        return _cors_json(request, {})
    if request.method not in {"GET", "PATCH"}:
        return _cors_json(request, {"ok": False, "error": "Method not allowed"}, status=405)

    patient = _authenticated_patient(request)
    if not patient:
        return _cors_json(
            request,
            {"ok": False, "error": "โทเคนไม่ถูกต้องหรือหมดอายุ กรุณาเข้าสู่ระบบใหม่"},
            status=401,
        )

    if request.method == "PATCH":
        payload, error, error_status = _json_body(request)
        if error:
            return _cors_json(request, {"ok": False, "error": error}, status=error_status)

        immutable_errors = {}
        if "national_id" in payload and str(payload.get("national_id") or "") != patient.national_id:
            immutable_errors["national_id"] = ["ไม่สามารถแก้ไขเลขบัตรประชาชนผ่าน Patient Portal ได้"]
        if "hn" in payload and str(payload.get("hn") or "") != patient.hn:
            immutable_errors["hn"] = ["ไม่สามารถแก้ไข HN ผ่าน Patient Portal ได้"]
        if immutable_errors:
            return _cors_json(
                request,
                {"ok": False, "error": "มีข้อมูลที่ไม่อนุญาตให้แก้ไข", "errors": immutable_errors},
                status=400,
            )

        errors = {}
        changes = {}

        for field in (
            "first_name", "last_name", "address", "province", "district",
            "subdistrict", "postal_code", "chronic_diseases", "allergies", "medications",
        ):
            if field in payload:
                value = str(payload.get(field) or "").strip()
                if field in {"first_name", "last_name"} and not value:
                    errors[field] = ["กรุณาระบุข้อมูลในช่องนี้"]
                else:
                    changes[field] = value

        if "gender" in payload:
            gender = str(payload.get("gender") or "").upper()
            valid_genders = {value for value, _ in Patient.GENDER_CHOICES}
            if gender not in valid_genders:
                errors["gender"] = ["เพศไม่ถูกต้อง"]
            else:
                changes["gender"] = gender

        if "blood_type" in payload:
            blood_type = str(payload.get("blood_type") or "").upper()
            valid_blood_types = {value for value, _ in Patient.BLOOD_CHOICES}
            if blood_type not in valid_blood_types:
                errors["blood_type"] = ["กรุ๊ปเลือดไม่ถูกต้อง"]
            else:
                changes["blood_type"] = blood_type

        if "birth_date" in payload:
            raw_birth_date = payload.get("birth_date")
            birth_date = parse_date(str(raw_birth_date)) if raw_birth_date else None
            if raw_birth_date and not birth_date:
                errors["birth_date"] = ["รูปแบบวันเกิดไม่ถูกต้อง"]
            elif birth_date and birth_date > timezone.localdate():
                errors["birth_date"] = ["วันเกิดต้องไม่เป็นวันที่ในอนาคต"]
            else:
                changes["birth_date"] = birth_date

        if "age" in payload and payload.get("age") is not None:
            try:
                age = int(payload.get("age"))
                if age < 0 or age > 130:
                    raise ValueError
                changes["age"] = age
            except (TypeError, ValueError):
                errors["age"] = ["กรุณาระบุอายุระหว่าง 0-130 ปี"]

        for field in ("height_cm", "weight_kg"):
            if field in payload:
                value = payload.get(field)
                if value in (None, ""):
                    changes[field] = None
                else:
                    try:
                        numeric = float(value)
                        if numeric <= 0 or numeric > 999:
                            raise ValueError
                        changes[field] = numeric
                    except (TypeError, ValueError):
                        errors[field] = ["ค่าตัวเลขไม่ถูกต้อง"]

        if "phone" in payload:
            phone = _normalize_phone(payload.get("phone"))
            if phone and not re.fullmatch(r"0[0-9]{8,9}", phone):
                errors["phone"] = ["รูปแบบเบอร์โทรศัพท์ไม่ถูกต้อง"]
            else:
                changes["phone"] = phone
                changes["phone_normalized"] = phone

        if "email" in payload:
            email = _normalize_email(payload.get("email")) or None
            if email:
                try:
                    validate_email(email)
                except ValidationError:
                    errors["email"] = ["รูปแบบอีเมลไม่ถูกต้อง"]
                else:
                    owner = Patient.objects.filter(email__iexact=email).exclude(pk=patient.pk).first()
                    if owner:
                        errors["email"] = ["อีเมลนี้ถูกใช้กับบัญชีอื่นแล้ว"]
            if "email" not in errors:
                changes["email"] = email
                if email != _normalize_email(patient.email):
                    changes["email_verified"] = False

        contacts, contacts_error = _validate_emergency_contacts(payload.get("emergency_contacts")) if "emergency_contacts" in payload else (None, None)
        if contacts_error:
            errors["emergency_contacts"] = [contacts_error]
        elif contacts is not None:
            changes["emergency_contacts"] = contacts
            first = contacts[0] if contacts else {}
            changes["emergency_name"] = first.get("name", "")
            changes["emergency_relationship"] = first.get("relationship", "")
            changes["emergency_phone"] = first.get("phone", "")
        else:
            for field in ("emergency_name", "emergency_relationship", "emergency_phone"):
                if field in payload:
                    value = str(payload.get(field) or "").strip()
                    if field == "emergency_phone":
                        value = _normalize_phone(value)
                    changes[field] = value

        if errors:
            return _cors_json(
                request,
                {"ok": False, "error": "กรุณาตรวจสอบข้อมูลที่แก้ไข", "errors": errors},
                status=400,
            )

        for field, value in changes.items():
            setattr(patient, field, value)

        if contacts is None and any(
            key in changes for key in ("emergency_name", "emergency_relationship", "emergency_phone")
        ):
            if patient.emergency_name or patient.emergency_phone:
                patient.emergency_contacts = [{
                    "id": "primary",
                    "name": patient.emergency_name,
                    "relationship": patient.emergency_relationship,
                    "phone": patient.emergency_phone,
                }]
            else:
                patient.emergency_contacts = []

        patient.save()
        patient.refresh_from_db()

    visits = list(
        Visit.objects.filter(patient=patient)
        .select_related("queue", "vitals", "opd_assessment")
        .order_by("-registered_at")[:20]
    )
    active_queue = (
        Queue.objects.select_related("visit")
        .filter(visit__patient=patient, status__in=ACTIVE_QUEUE_STATUSES)
        .order_by("-created_at")
        .first()
    )
    appointments = [
        {
            "date": appointment.date.isoformat(),
            "time": appointment.time.strftime("%H:%M") if appointment.time else None,
            "status": appointment.status,
            "status_label": appointment.get_status_display(),
            "note": appointment.note,
        }
        for appointment in patient.appointments.order_by("-date", "-time")[:20]
    ]

    response_payload = {
        "ok": True,
        "profile": _patient_profile_payload(patient, mask_national_id=False),
        "active_queue": _serialize_queue(active_queue) if active_queue else None,
        "visits": [_serialize_visit(visit) for visit in visits],
        "appointments": appointments,
    }
    if request.method == "PATCH":
        response_payload["message"] = "บันทึกข้อมูลเรียบร้อยแล้ว"
    return _cors_json(request, response_payload)


@csrf_exempt
def patient_queue(request):
    if request.method == "OPTIONS":
        return _cors_json(request, {})
    if request.method != "GET":
        return _cors_json(request, {"ok": False, "error": "Method not allowed"}, status=405)

    patient = _authenticated_patient(request)
    if not patient:
        return _cors_json(
            request,
            {"ok": False, "error": "โทเคนไม่ถูกต้องหรือหมดอายุ กรุณาเข้าสู่ระบบใหม่"},
            status=401,
        )
    queue = (
        Queue.objects.select_related("visit")
        .filter(visit__patient=patient, status__in=ACTIVE_QUEUE_STATUSES)
        .order_by("-created_at")
        .first()
    )
    if not queue:
        return _cors_json(
            request,
            {"ok": True, "queue_number": None, "message": "ไม่มีคิวที่กำลังรอรับบริการในวันนี้"},
        )
    return _cors_json(request, {"ok": True, **_serialize_queue(queue)})


@csrf_exempt
def patient_cancel_queue(request):
    if request.method == "OPTIONS":
        return _cors_json(request, {})
    if request.method != "POST":
        return _cors_json(request, {"ok": False, "error": "Method not allowed"}, status=405)

    patient = _authenticated_patient(request)
    if not patient:
        return _cors_json(
            request,
            {"ok": False, "error": "โทเคนไม่ถูกต้องหรือหมดอายุ กรุณาเข้าสู่ระบบใหม่"},
            status=401,
        )

    with transaction.atomic():
        queue = (
            Queue.objects.select_for_update()
            .filter(visit__patient=patient)
            .order_by("-created_at")
            .first()
        )
        if not queue:
            return _cors_json(request, {"ok": False, "error": "ไม่พบคิวที่กำลังใช้งาน"}, status=409)
        if queue.status == Queue.Status.CANCELLED:
            return _cors_json(request, {"ok": True, "message": "คิวนี้ถูกยกเลิกแล้ว"})
        if queue.status not in PATIENT_CANCELLABLE_QUEUE_STATUSES:
            return _cors_json(
                request,
                {"ok": False, "error": "สถานะคิวปัจจุบันไม่สามารถยกเลิกด้วยตนเองได้ กรุณาติดต่อเจ้าหน้าที่"},
                status=409,
            )

        queue.status = Queue.Status.CANCELLED
        queue.save(update_fields=["status"])

    return _cors_json(request, {"ok": True, "message": "ยกเลิกคิวเรียบร้อยแล้ว"})


def _staff_emergency_contacts_from_post(data):
    contacts = []
    for index in range(1, 4):
        name = str(data.get(f"emergency_name_{index}") or "").strip()
        relationship = str(data.get(f"emergency_relationship_{index}") or "").strip().upper()
        phone = normalize_thai_phone(data.get(f"emergency_phone_{index}"))
        if not (name or relationship or phone):
            continue
        contacts.append({
            "id": f"staff-{index}",
            "name": name[:120],
            "relationship": relationship[:20],
            "phone": phone[:20],
        })
    if contacts:
        return contacts

    # Backward-compatible fallback for older single-contact submissions.
    name = str(data.get("emergency_name") or "").strip()
    relationship = str(data.get("emergency_relationship") or "").strip().upper()
    phone = normalize_thai_phone(data.get("emergency_phone"))
    if name or relationship or phone:
        return [{
            "id": "staff-1",
            "name": name[:120],
            "relationship": relationship[:20],
            "phone": phone[:20],
        }]
    return []


def _staff_emergency_contacts_for_patient(patient):
    contacts = patient.emergency_contacts if isinstance(patient.emergency_contacts, list) else []
    normalized = []
    for index, contact in enumerate(contacts[:3], start=1):
        if not isinstance(contact, dict):
            continue
        normalized.append({
            "id": str(contact.get("id") or f"staff-{index}"),
            "name": str(contact.get("name") or ""),
            "relationship": str(contact.get("relationship") or ""),
            "phone": str(contact.get("phone") or ""),
        })
    if normalized:
        return normalized
    if patient.emergency_name or patient.emergency_relationship or patient.emergency_phone:
        return [{
            "id": "staff-1",
            "name": patient.emergency_name or "",
            "relationship": patient.emergency_relationship or "",
            "phone": patient.emergency_phone or "",
        }]
    return [{"id": "staff-1", "name": "", "relationship": "", "phone": ""}]


def _apply_staff_emergency_contacts(patient, contacts):
    patient.emergency_contacts = contacts
    primary = contacts[0] if contacts else {}
    patient.emergency_name = primary.get("name", "")
    patient.emergency_relationship = primary.get("relationship", "")
    patient.emergency_phone = primary.get("phone", "")


def _registration_context(
    form,
    *,
    is_edit=False,
    patient=None,
    emergency_contacts=None,
    existing_query="",
    existing_results=None,
    selected_patient=None,
):
    if emergency_contacts is None:
        emergency_contacts = (
            _staff_emergency_contacts_for_patient(patient)
            if patient
            else [{"id": "staff-1", "name": "", "relationship": "", "phone": ""}]
        )
    optional_fields = {
        "height_cm", "weight_kg", "bp_sys", "bp_dia", "blood_type",
        "chronic_diseases", "allergies", "medications", "address",
        "province", "district", "subdistrict", "postal_code",
        "emergency_name", "emergency_relationship", "emergency_phone",
    }
    return {
        "form": form,
        "is_edit": is_edit,
        "patient": patient,
        "emergency_contacts": emergency_contacts,
        "existing_query": existing_query,
        "existing_results": existing_results if existing_results is not None else [],
        "selected_patient": selected_patient,
        # Do not hide validation feedback inside a collapsed optional section.
        "open_optional_details": is_edit or bool(optional_fields.intersection(form.errors)),
    }


def _after_patient_change(request, patient):
    """Continue in the signed-in user's workflow instead of leaking into another role."""
    if has_capability(request.user, Capability.RECORD_VITALS):
        return redirect("waiting_vitals")
    if has_capability(request.user, Capability.VIEW_PATIENT):
        return redirect("patient_history", patient_id=patient.id)
    return redirect("role_landing")


@login_required
def register_patient(request):
    if request.method == "POST":
        selected_patient = None
        selected_patient_id = str(request.POST.get("existing_patient_id") or "").strip()
        if selected_patient_id.isdigit():
            selected_patient = Patient.objects.filter(pk=int(selected_patient_id)).first()

        form = PatientForm(request.POST, instance=selected_patient, allow_existing=True)
        emergency_contacts = _staff_emergency_contacts_from_post(request.POST)

        if not form.is_valid():
            return render(
                request,
                "patients/register.html",
                _registration_context(
                    form,
                    emergency_contacts=emergency_contacts,
                    selected_patient=selected_patient,
                ),
            )

        national_id = form.cleaned_data["national_id"]
        if selected_patient and national_id != selected_patient.national_id:
            form.add_error("national_id", "เลขบัตรประชาชนไม่ตรงกับผู้ป่วยเดิมที่เลือก")
            return render(
                request,
                "patients/register.html",
                _registration_context(
                    form,
                    emergency_contacts=emergency_contacts,
                    selected_patient=selected_patient,
                ),
            )

        with transaction.atomic():
            if selected_patient:
                patient = Patient.objects.select_for_update().get(pk=selected_patient.pk)
            else:
                patient = Patient.objects.select_for_update().filter(national_id=national_id).first()

            if patient:
                active_queue = (
                    Queue.objects.filter(
                        visit__patient=patient,
                        status__in=ACTIVE_QUEUE_STATUSES,
                    )
                    .order_by("-created_at")
                    .first()
                )
                if active_queue:
                    form.add_error(
                        None,
                        f"ผู้ป่วยมีคิว {active_queue.display_number} ที่กำลังรับบริการอยู่แล้ว กรุณาตรวจสอบคิวเดิม",
                    )
                    return render(
                        request,
                        "patients/register.html",
                        _registration_context(
                            form,
                            emergency_contacts=emergency_contacts,
                            selected_patient=patient if selected_patient else None,
                        ),
                    )

                for field, value in form.cleaned_data.items():
                    setattr(patient, field, value)
                _apply_staff_emergency_contacts(patient, emergency_contacts)
                patient.save()
            else:
                patient = Patient(**form.cleaned_data)
                _apply_staff_emergency_contacts(patient, emergency_contacts)
                patient.save()

            visit = Visit.objects.create(
                patient=patient,
                registered_at=timezone.now(),
                note=patient.note,
            )

            VitalSign.objects.create(
                visit=visit,
                sys_bp=patient.bp_sys,
                dia_bp=patient.bp_dia,
            )

            Queue.objects.create(
                visit=visit,
                status=Queue.Status.WAITING_VITALS,
            )

        return _after_patient_change(request, patient)

    existing_query = request.GET.get("existing_q", "").strip()
    selected_patient_id = request.GET.get("existing_patient", "").strip()
    selected_patient = None
    if selected_patient_id.isdigit():
        selected_patient = Patient.objects.filter(pk=int(selected_patient_id)).first()

    existing_results = Patient.objects.none()
    if existing_query:
        existing_results = (
            Patient.objects.filter(
                Q(hn__icontains=existing_query)
                | Q(first_name__icontains=existing_query)
                | Q(last_name__icontains=existing_query)
                | Q(national_id__icontains=existing_query)
                | Q(phone__icontains=existing_query)
            )
            .order_by("first_name", "last_name")[:8]
        )

    form = PatientForm(
        instance=selected_patient,
        initial={"note": ""} if selected_patient else None,
        allow_existing=True,
    )
    return render(
        request,
        "patients/register.html",
        _registration_context(
            form,
            patient=selected_patient,
            existing_query=existing_query,
            existing_results=existing_results,
            selected_patient=selected_patient,
        ),
    )


@login_required
def edit_patient(request, patient_id):
    patient = get_object_or_404(Patient, pk=patient_id)
    if request.method == "POST":
        form = PatientForm(request.POST, instance=patient)
        emergency_contacts = _staff_emergency_contacts_from_post(request.POST)
        if form.is_valid():
            patient = form.save(commit=False)
            _apply_staff_emergency_contacts(patient, emergency_contacts)
            patient.save()
            messages.success(request, "แก้ไขข้อมูลผู้ป่วยเรียบร้อยแล้ว")
            return _after_patient_change(request, patient)
    else:
        form = PatientForm(instance=patient)
        emergency_contacts = _staff_emergency_contacts_for_patient(patient)

    return render(
        request,
        "patients/register.html",
        _registration_context(
            form,
            is_edit=True,
            patient=patient,
            emergency_contacts=emergency_contacts,
        ),
    )


@login_required
@require_POST
def update_patient_birth_date(request, patient_id):
    patient = get_object_or_404(Patient, pk=patient_id)
    form = PatientBirthDateForm(request.POST, instance=patient)
    if form.is_valid():
        form.save()
        messages.success(request, f"บันทึกวันเกิดแล้ว อายุปัจจุบันคือ {patient.age_display}")
    else:
        error = form.errors.get("birth_date", ["กรุณาตรวจสอบวันเกิด"])[0]
        messages.error(request, str(error))
    return _after_patient_change(request, patient)


@login_required
def patient_search(request):
    query = request.GET.get("q", "").strip()
    patients = Patient.objects.none()

    if query:
        patients = (
            Patient.objects
            .filter(
                Q(hn__icontains=query)
                | Q(first_name__icontains=query)
                | Q(last_name__icontains=query)
                | Q(national_id__icontains=query)
                | Q(phone__icontains=query)
            )
            .order_by("hn", "first_name")[:80]
        )

    return render(request, "patients/search.html", {
        "query": query,
        "patients": patients,
    })


@login_required
def patient_history(request, patient_id: int):
    patient = get_object_or_404(Patient, id=patient_id)
    visits = list(
        Visit.objects
        .filter(patient=patient)
        .select_related(
            "queue",
            "triage_result",
            "opd_assessment",
            "opd_assessment__examiner",
            "vitals",
            "prescription",
            "bill",
        )
        .prefetch_related(
            "critical_alerts",
            Prefetch(
                "workflow_logs",
                queryset=VisitWorkflowLog.objects.select_related("actor").order_by("-created_at", "-id"),
            ),
        )
        .order_by("-registered_at")
    )
    for visit in visits:
        logs = list(visit.workflow_logs.all())
        visit.workflow_timeline = logs
        visit.vitals_operator_log = next(
            (log for log in logs if log.event_type == VisitWorkflowLog.EventType.VITALS_RECORDED),
            None,
        )
        visit.triage_operator_log = next(
            (log for log in logs if log.event_type == VisitWorkflowLog.EventType.TRIAGE_CONFIRMED),
            None,
        )
        visit.patient_journey = _patient_journey_for_visit(visit, logs)
    appointments = patient.appointments.order_by("-date", "-time", "-created_at")

    return render(request, "patients/history.html", {
        "patient": patient,
        "visits": visits,
        "appointments": appointments,
        "appointment_statuses": Appointment.Status.choices,
    })


@login_required
@require_POST
def create_appointment(request, patient_id: int):
    patient = get_object_or_404(Patient, id=patient_id)
    date = request.POST.get("date")
    time = request.POST.get("time") or None
    note = request.POST.get("note", "").strip()
    if date:
        Appointment.objects.create(patient=patient, date=date, time=time, note=note)
    return redirect("patient_history", patient_id=patient.id)


@login_required
@require_POST
def update_appointment_status(request, appointment_id: int):
    appointment = get_object_or_404(Appointment, id=appointment_id)
    status = request.POST.get("status")
    if status in Appointment.Status.values:
        appointment.status = status
        appointment.attended_at = timezone.now() if status == Appointment.Status.ATTENDED else None
        appointment.save(update_fields=["status", "attended_at", "updated_at"])
    return redirect("patient_history", patient_id=appointment.patient_id)
