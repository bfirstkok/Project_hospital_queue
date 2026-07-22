# patients/views.py
from django.contrib.auth.decorators import login_required
from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist
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
import re
import secrets

from .forms import PatientForm, PublicPatientRegistrationForm
from .models import Appointment, Patient, PatientAccessToken
from .security import rate_limited
from queues.models import Visit, Queue, VitalSign


ACTIVE_QUEUE_STATUSES = {
    Queue.Status.WAITING_VITALS,
    Queue.Status.WAITING_CONFIRMATION,
    Queue.Status.WAITING_QUEUE,
    Queue.Status.WAITING,
    Queue.Status.CALLED,
    Queue.Status.MONITORING,
    Queue.Status.FOLLOWUP,
}

PUBLIC_STATUS = {
    Queue.Status.WAITING_VITALS: ("รอตรวจวัดสัญญาณชีพ", "กรุณาไปยังจุดวัดสัญญาณชีพ"),
    Queue.Status.WAITING_CONFIRMATION: ("รอพยาบาลยืนยันผลคัดกรอง", "กรุณารอบริเวณจุดคัดกรอง"),
    Queue.Status.WAITING_QUEUE: ("รอเรียกคิว", "กรุณารอบริเวณหน้าห้องตรวจ"),
    Queue.Status.WAITING: ("รอเรียกคิว", "กรุณารอบริเวณหน้าห้องตรวจ"),
    Queue.Status.CALLED: ("กรุณาเข้าห้องตรวจ", "ถึงคิวของคุณแล้ว กรุณาเข้าห้องตรวจ"),
    Queue.Status.MONITORING: ("กำลังรับบริการ", "อยู่ระหว่างการติดตามอาการ"),
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


def _queue_number(visit):
    return f"Q-{visit.id:04d}"


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
        "queue_number": _queue_number(queue.visit),
        "status": queue.status,
        "status_label": label,
        "instruction": instruction,
        "room": f"ห้องตรวจ {queue.exam_room}" if queue.exam_room else None,
        "people_ahead": _people_ahead(queue),
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
        "queue_number": _queue_number(visit),
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
        if not created:
            for field, value in form.cleaned_data.items():
                if field != "consent":
                    setattr(patient, field, value)
            patient.save()

        active_visit = (
            Visit.objects.select_related("queue")
            .filter(patient=patient, queue__status__in=ACTIVE_QUEUE_STATUSES)
            .order_by("-registered_at")
            .first()
        )
        if active_visit:
            visit = active_visit
        else:
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
    }, status=201 if not active_visit else 200)


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
        "queue_number": _queue_number(visit),
        "status": queue.status,
        "status_label": label,
        "instruction": instruction,
        "people_ahead": _people_ahead(queue),
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
            "age": patient.age,
            "phone": patient.phone,
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
        .filter(visit__patient=patient)
        .order_by("-created_at")
        .first()
    )
    if not queue:
        return _cors_json(request, {"ok": False, "error": "ไม่พบข้อมูลคิวของผู้ป่วย"}, status=404)
    return _cors_json(request, {"ok": True, **_serialize_queue(queue)})


@login_required
def register_patient(request):
    if request.method == "POST":
        form = PatientForm(request.POST)

        if not form.is_valid():
            return render(request, "patients/register.html", {"form": form})

        national_id = form.cleaned_data["national_id"]

        with transaction.atomic():
            patient, created = Patient.objects.get_or_create(
                national_id=national_id,
                defaults=form.cleaned_data,  # ตอนสร้างใหม่ ใส่ทุก field ได้เลย
            )

            # ถ้ามีอยู่แล้ว → อัปเดตข้อมูลจากฟอร์ม (ให้หน้า register ใช้แก้ข้อมูลคนเดิมได้)
            if not created:
                for field, value in form.cleaned_data.items():
                    setattr(patient, field, value)
                patient.save()

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
