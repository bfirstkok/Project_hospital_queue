# patients/views.py
from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.core import signing
from django.core.cache import cache
from django.db.models import Q
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, render, redirect
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
import json

from .forms import PatientForm, PublicPatientRegistrationForm
from .models import Appointment, Patient
from queues.models import Visit, Queue, VitalSign


ACTIVE_QUEUE_STATUSES = {
    Queue.Status.WAITING_VITALS,
    Queue.Status.WAITING_CONFIRMATION,
    Queue.Status.WAITING_QUEUE,
    Queue.Status.WAITING,
    Queue.Status.CALLED,
    Queue.Status.MONITORING,
}

PATIENT_TOKEN_SALT = "patients.public-api"


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
    from django.conf import settings
    if origin and origin in settings.PATIENT_APP_ORIGINS:
        response["Access-Control-Allow-Origin"] = origin
        response["Vary"] = "Origin"
    response["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    response["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
    return response


def _client_ip(request):
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    return (forwarded.split(",", 1)[0] if forwarded else request.META.get("REMOTE_ADDR", "unknown")).strip()


def _patient_token(patient):
    return signing.dumps(
        {"patient_id": patient.pk, "national_id": patient.national_id},
        salt=PATIENT_TOKEN_SALT,
        compress=True,
    )


def _patient_from_request(request):
    authorization = request.headers.get("Authorization", "")
    scheme, separator, token = authorization.partition(" ")
    if not separator or scheme.lower() != "bearer" or not token.strip():
        return None

    try:
        payload = signing.loads(
            token.strip(),
            salt=PATIENT_TOKEN_SALT,
            max_age=settings.PATIENT_TOKEN_MAX_AGE,
        )
        patient_id = payload["patient_id"]
        national_id = payload["national_id"]
    except (KeyError, TypeError, signing.BadSignature, signing.SignatureExpired):
        return None

    try:
        return Patient.objects.get(pk=patient_id, national_id=national_id)
    except Patient.DoesNotExist:
        return None


def _masked_national_id(national_id):
    if len(national_id) != 13:
        return ""
    return f"{national_id[0]}-xxxx-xxxxx-xx-{national_id[-1]}"


def _queue_number(visit):
    return f"Q-{visit.id:04d}"


def _people_ahead(queue):
    if queue.status not in {Queue.Status.WAITING_QUEUE, Queue.Status.WAITING}:
        return 0
    confirmed_at = queue.visit.confirmed_at or queue.created_at
    return Queue.objects.filter(
        status__in=[Queue.Status.WAITING_QUEUE, Queue.Status.WAITING],
    ).filter(
        Q(priority__lt=queue.priority)
        | Q(priority=queue.priority, visit__confirmed_at__lt=confirmed_at)
        | Q(priority=queue.priority, visit__confirmed_at=confirmed_at, created_at__lt=queue.created_at)
    ).count()


def _serialize_queue(visit):
    queue = getattr(visit, "queue", None)
    if queue is None:
        return None
    label, instruction = PUBLIC_STATUS.get(
        queue.status,
        ("กำลังตรวจสอบสถานะ", "กรุณาติดต่อเจ้าหน้าที่"),
    )
    return {
        "queue_number": _queue_number(visit),
        "status": queue.status,
        "status_label": label,
        "instruction": instruction,
        "people_ahead": _people_ahead(queue),
        "room": f"ห้องตรวจ {queue.exam_room}" if queue.exam_room else None,
        "updated_at": timezone.now().isoformat(),
    }


def _serialize_visit(visit):
    queue = getattr(visit, "queue", None)
    vitals = getattr(visit, "vitals", None)
    assessment = getattr(visit, "opd_assessment", None)
    status = queue.status if queue else None
    status_label = PUBLIC_STATUS.get(
        status,
        ("กำลังตรวจสอบสถานะ", ""),
    )[0]

    vital_payload = None
    if vitals:
        vital_payload = {
            "rr": vitals.rr,
            "pr": vitals.pr,
            "sys_bp": vitals.sys_bp,
            "dia_bp": vitals.dia_bp,
            "bt": vitals.bt,
            "o2sat": vitals.o2sat,
        }

    return {
        "queue_number": _queue_number(visit),
        "status": status,
        "status_label": status_label,
        "registered_at": visit.registered_at.isoformat(),
        "note": visit.note or "",
        "diagnosis": assessment.diagnosis if assessment else "",
        "treatment": assessment.treatment if assessment else "",
        "vitals": vital_payload,
    }


def _serialize_profile(patient):
    address = " ".join(
        str(value).strip()
        for value in [
            patient.address,
            patient.subdistrict,
            patient.district,
            patient.province,
            patient.postal_code,
        ]
        if value
    )
    return {
        "first_name": patient.first_name,
        "last_name": patient.last_name,
        "national_id": _masked_national_id(patient.national_id),
        "hn": patient.hn,
        "phone": patient.phone,
        "gender": patient.get_gender_display(),
        "age": patient.age,
        "blood_type": patient.get_blood_type_display(),
        "height_cm": float(patient.height_cm) if patient.height_cm is not None else None,
        "weight_kg": float(patient.weight_kg) if patient.weight_kg is not None else None,
        "address": address,
        "chronic_diseases": patient.chronic_diseases,
        "allergies": patient.allergies,
        "medications": patient.medications,
        "emergency_name": patient.emergency_name,
        "emergency_phone": patient.emergency_phone,
    }


def _serialize_appointment(appointment):
    return {
        "date": appointment.date.isoformat(),
        "time": appointment.time.strftime("%H:%M") if appointment.time else None,
        "status": appointment.status,
        "status_label": appointment.get_status_display(),
        "note": appointment.note,
    }


@csrf_exempt
def public_register(request):
    if request.method == "OPTIONS":
        return _cors_json(request, {})
    if request.method != "POST":
        return _cors_json(request, {"ok": False, "error": "Method not allowed"}, status=405)
    if int(request.META.get("CONTENT_LENGTH") or 0) > 16384:
        return _cors_json(request, {"ok": False, "error": "ข้อมูลมีขนาดใหญ่เกินไป"}, status=413)

    throttle_key = f"patient-register:{_client_ip(request)}"
    request_count = cache.get(throttle_key, 0)
    if request_count >= 30:
        return _cors_json(request, {"ok": False, "error": "ส่งข้อมูลบ่อยเกินไป กรุณารอสักครู่"}, status=429)
    cache.set(throttle_key, request_count + 1, 300)

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

    return _cors_json(request, {
        "ok": True,
        "tracking_token": str(visit.tracking_token),
        "access_token": _patient_token(patient),
        "token_type": "Bearer",
        "expires_in": settings.PATIENT_TOKEN_MAX_AGE,
        "queue_number": _queue_number(visit),
        "status_url": f"/api/patient/queue/{visit.tracking_token}/",
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
def public_login(request):
    if request.method == "OPTIONS":
        return _cors_json(request, {})
    if request.method != "POST":
        return _cors_json(request, {"ok": False, "error": "Method not allowed"}, status=405)
    if int(request.META.get("CONTENT_LENGTH") or 0) > 4096:
        return _cors_json(request, {"ok": False, "error": "ข้อมูลมีขนาดใหญ่เกินไป"}, status=413)

    throttle_key = f"patient-login:{_client_ip(request)}"
    request_count = cache.get(throttle_key, 0)
    if request_count >= 20:
        return _cors_json(
            request,
            {"ok": False, "error": "เข้าสู่ระบบบ่อยเกินไป กรุณารอสักครู่"},
            status=429,
        )
    cache.set(throttle_key, request_count + 1, 300)

    try:
        payload = json.loads(request.body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return _cors_json(request, {"ok": False, "error": "รูปแบบข้อมูลไม่ถูกต้อง"}, status=400)

    national_id = str(payload.get("national_id") or "").strip()
    if not national_id.isdigit() or len(national_id) != 13:
        return _cors_json(
            request,
            {"ok": False, "error": "เลขบัตรประชาชนต้องเป็นตัวเลข 13 หลัก"},
            status=400,
        )

    try:
        patient = Patient.objects.get(national_id=national_id)
    except Patient.DoesNotExist:
        return _cors_json(
            request,
            {"ok": False, "error": "เลขบัตรประชาชนไม่ถูกต้องหรือไม่พบข้อมูล"},
            status=401,
        )

    return _cors_json(request, {
        "ok": True,
        "access_token": _patient_token(patient),
        "token_type": "Bearer",
        "expires_in": settings.PATIENT_TOKEN_MAX_AGE,
    })


@csrf_exempt
def public_me(request):
    if request.method == "OPTIONS":
        return _cors_json(request, {})
    if request.method != "GET":
        return _cors_json(request, {"ok": False, "error": "Method not allowed"}, status=405)

    patient = _patient_from_request(request)
    if patient is None:
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
    active_visit = next(
        (
            visit
            for visit in visits
            if getattr(visit, "queue", None)
            and visit.queue.status in ACTIVE_QUEUE_STATUSES
        ),
        None,
    )
    appointments = patient.appointments.order_by("-date", "-time", "-created_at")[:20]

    return _cors_json(request, {
        "ok": True,
        "profile": _serialize_profile(patient),
        "active_queue": _serialize_queue(active_visit) if active_visit else None,
        "visits": [_serialize_visit(visit) for visit in visits],
        "appointments": [_serialize_appointment(item) for item in appointments],
    })


@csrf_exempt
def public_authenticated_queue(request):
    if request.method == "OPTIONS":
        return _cors_json(request, {})
    if request.method != "GET":
        return _cors_json(request, {"ok": False, "error": "Method not allowed"}, status=405)

    patient = _patient_from_request(request)
    if patient is None:
        return _cors_json(
            request,
            {"ok": False, "error": "โทเคนไม่ถูกต้องหรือหมดอายุ กรุณาเข้าสู่ระบบใหม่"},
            status=401,
        )

    visit = (
        Visit.objects.filter(
            patient=patient,
            queue__status__in=ACTIVE_QUEUE_STATUSES,
        )
        .select_related("queue")
        .order_by("-registered_at")
        .first()
    )
    if visit is None:
        return _cors_json(
            request,
            {"ok": False, "error": "ไม่มีคิวที่กำลังดำเนินการ"},
            status=404,
        )

    return _cors_json(request, {"ok": True, **_serialize_queue(visit)})


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
