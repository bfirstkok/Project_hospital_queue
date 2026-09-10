# patients/views.py
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.hashers import check_password, make_password
from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist
from django.core.mail import send_mail
from django.db.models import Q
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, render, redirect
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from datetime import timedelta
import hashlib
import json
import logging
import re
import secrets

from .forms import PatientBirthDateForm, PatientForm, PublicPatientRegistrationForm
from .models import Appointment, OtpChallenge, Patient, PatientAccessToken, PatientPin
from .security import rate_limited, rate_limited_by_identifier
from queues.models import Visit, Queue, VitalSign


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
    Queue.Status.OPD_DONE: ("เสร็จสิ้นการรับบริการ", "การตรวจ OPD เสร็จสิ้นแล้ว"),
    Queue.Status.FOLLOWUP: ("นัดติดตามอาการ", "กรุณาตรวจสอบวันนัดกับเจ้าหน้าที่"),
    Queue.Status.DISCHARGED: ("เสร็จสิ้นการรับบริการ", "สามารถกลับบ้านได้ตามคำแนะนำของเจ้าหน้าที่"),
    Queue.Status.CANCELLED: ("ยกเลิกคิวแล้ว", "หากต้องการรับบริการ กรุณาติดต่อเจ้าหน้าที่"),
}


def _cors_json(request, payload, status=200):
    response = JsonResponse(payload, status=status)
    origin = request.headers.get("Origin", "")
    if origin and origin in settings.PATIENT_APP_ORIGINS:
        response["Access-Control-Allow-Origin"] = origin
        from django.utils.cache import patch_vary_headers
        patch_vary_headers(response, ["Origin"])
    response["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    response["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
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
    if not access_token or access_token.expires_at <= now:
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
    if int(request.META.get("CONTENT_LENGTH") or 0) > 16384:
        return _cors_json(request, {"ok": False, "error": "ข้อมูลมีขนาดใหญ่เกินไป"}, status=413)

    if rate_limited(request, "patient-register", limit=30, window_seconds=300):
        return _cors_json(request, {"ok": False, "error": "ส่งข้อมูลบ่อยเกินไป กรุณารอสักครู่"}, status=429)

    try:
        payload = json.loads(request.body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return _cors_json(request, {"ok": False, "error": "รูปแบบข้อมูลไม่ถูกต้อง"}, status=400)

    if payload.get("website"):
        return _cors_json(request, {"ok": True}, status=202)

    form = PublicPatientRegistrationForm(payload)
    if not form.is_valid():
        errors = {field: [str(message) for message in messages] for field, messages in form.errors.items()}
        return _cors_json(request, {"ok": False, "errors": errors}, status=400)

    with transaction.atomic():
        patient, created = Patient.objects.select_for_update().get_or_create(
            national_id=form.cleaned_data["national_id"],
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
                if field != "consent":
                    if field == "birth_date" and "birth_date" not in payload:
                        continue
                    setattr(patient, field, value)
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
        "status_url": f"/api/patient/queue/{visit.tracking_token}/",
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

    if rate_limited(request, "patient-login", limit=10, window_seconds=300):
        return _cors_json(
            request,
            {"ok": False, "error": "พยายามเข้าสู่ระบบบ่อยเกินไป กรุณารอสักครู่"},
            status=429,
        )
    payload, error, error_status = _json_body(request)
    if error:
        return _cors_json(request, {"ok": False, "error": error}, status=error_status)
    national_id = str(payload.get("national_id") or "").strip()
    if not re.fullmatch(r"[0-9]{13}", national_id):
        return _cors_json(
            request,
            {"ok": False, "error": "ข้อมูลเข้าสู่ระบบไม่ถูกต้อง"},
            status=400,
        )

    patient = Patient.objects.filter(national_id=national_id).first()
    if not patient:
        return _cors_json(
            request,
            {"ok": False, "error": "ข้อมูลเข้าสู่ระบบไม่ถูกต้อง"},
            status=401,
        )

    access_token, expires_at = _issue_patient_token(patient)
    return _cors_json(request, {
        "ok": True,
        "access_token": access_token,
        "token_type": "Bearer",
        "expires_in": int(getattr(settings, "PATIENT_TOKEN_MAX_AGE", 60 * 60 * 12)),
        "expires_at": expires_at.isoformat(),
    })


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
    generic_response = {"ok": True, "resend_after_seconds": 60}
    if not patient or not patient.email:
        return _cors_json(request, generic_response)

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
    pin = str(payload.get("pin") or "")
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
    if request.method != "GET":
        return _cors_json(request, {"ok": False, "error": "Method not allowed"}, status=405)

    patient = _authenticated_patient(request)
    if not patient:
        return _cors_json(
            request,
            {"ok": False, "error": "โทเคนไม่ถูกต้องหรือหมดอายุ กรุณาเข้าสู่ระบบใหม่"},
            status=401,
        )

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
    address = " ".join(filter(None, [
        patient.address,
        patient.subdistrict,
        patient.district,
        patient.province,
        patient.postal_code,
    ]))
    return _cors_json(request, {
        "ok": True,
        "profile": {
            "hn": patient.hn,
            "first_name": patient.first_name,
            "last_name": patient.last_name,
            "national_id": _masked_national_id(patient.national_id),
            "gender": patient.get_gender_display(),
            "birth_date": patient.birth_date.isoformat() if patient.birth_date else None,
            "age": patient.age_years,
            "age_display": patient.age_display,
            "phone": patient.phone,
            "email": patient.email,
            "blood_type": patient.get_blood_type_display(),
            "height_cm": patient.height_cm,
            "weight_kg": patient.weight_kg,
            "chronic_diseases": patient.chronic_diseases,
            "allergies": patient.allergies,
            "medications": patient.medications,
            "address": address,
            "emergency_name": patient.emergency_name,
            "emergency_phone": patient.emergency_phone,
        },
        "active_queue": _serialize_queue(active_queue) if active_queue else None,
        "visits": [_serialize_visit(visit) for visit in visits],
        "appointments": appointments,
    })


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
        return _cors_json(request, {"ok": False, "error": "ไม่พบข้อมูลคิวของผู้ป่วย"}, status=404)
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
            return _cors_json(request, {"ok": False, "error": "ไม่พบข้อมูลคิวของผู้ป่วย"}, status=404)
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


@login_required
def register_patient(request):
    if request.method == "POST":
        form = PatientForm(request.POST, allow_existing=True)

        if not form.is_valid():
            return render(request, "patients/register.html", {"form": form})

        national_id = form.cleaned_data["national_id"]

        with transaction.atomic():
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
                    return render(request, "patients/register.html", {"form": form})

                for field, value in form.cleaned_data.items():
                    setattr(patient, field, value)
                patient.save()
            else:
                patient = Patient.objects.create(**form.cleaned_data)

            # สร้าง Visit ใหม่ทุกครั้ง
            visit = Visit.objects.create(
                patient=patient,
                registered_at=timezone.now(),
                note=patient.note,
            )

            # สร้าง VitalSign จากข้อมูลที่กรอกในฟอร์ม
            VitalSign.objects.create(
                visit=visit,
                sys_bp=patient.bp_sys,
                dia_bp=patient.bp_dia,
            )

            # Queue starts outside the prioritized examination queue.
            Queue.objects.create(
                visit=visit,
                status=Queue.Status.WAITING_VITALS,
            )

        return redirect("waiting_vitals")

    # GET
    return render(request, "patients/register.html", {"form": PatientForm()})


@login_required
def edit_patient(request, patient_id):
    patient = get_object_or_404(Patient, pk=patient_id)
    if request.method == "POST":
        form = PatientForm(request.POST, instance=patient)
        if form.is_valid():
            form.save()
            messages.success(request, "แก้ไขข้อมูลผู้ป่วยเรียบร้อยแล้ว")
            return redirect("waiting_vitals")
    else:
        form = PatientForm(instance=patient)

    return render(
        request,
        "patients/register.html",
        {"form": form, "is_edit": True, "patient": patient},
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
    return redirect("waiting_vitals")


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
    visits = (
        Visit.objects
        .filter(patient=patient)
        .select_related("queue", "triage_result", "opd_assessment", "vitals")
        .prefetch_related("critical_alerts")
        .order_by("-registered_at")
    )
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
