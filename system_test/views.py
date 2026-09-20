import json
import random
import secrets
from datetime import date, timedelta

from django.apps import apps
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db import models, transaction
from django.db.models import Q
from django.forms import modelform_factory
from django.test import RequestFactory
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import NoReverseMatch, reverse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from accounts.access import CAPABILITY_LABELS, ROLE_CAPABILITIES, ROLE_DESCRIPTIONS, superuser_required
from queues.models import StaffProfile
from patients.models import Patient
from queues.models import (
    Device,
    DeviceAssignment,
    Queue,
    TelemetryLog,
    TriageResult,
    Visit,
    VitalSign,
)
from queues.views import create_critical_alerts_for_visit, iot_vitals

from .models import TestScenarioRun


VISIBLE_APPS = {"accounts", "admin", "auth", "dashboard", "opd", "patients", "queues", "sessions", "system_test"}
SENSITIVE_PARTS = ("password", "secret", "token", "api_key", "pin_hash", "code_hash")


def _allowed_model(app_label, model_name):
    try:
        model = apps.get_model(app_label, model_name)
    except LookupError as exc:
        raise Http404("ไม่พบตาราง") from exc
    if model._meta.auto_created or model._meta.app_label not in VISIBLE_APPS:
        raise Http404("ตารางนี้ไม่อนุญาตให้เปิดดู")
    return model


def _is_sensitive_field(field):
    return any(part in field.name.lower() for part in SENSITIVE_PARTS)


def _editable_field_names(model):
    """Expose ordinary model fields, but never credentials or generated identifiers."""
    return [
        field.name
        for field in model._meta.concrete_fields
        if field.editable
        and not field.primary_key
        and not _is_sensitive_field(field)
    ]


def _model_catalog():
    catalog = []
    for model in apps.get_models():
        if model._meta.auto_created or model._meta.app_label not in VISIBLE_APPS:
            continue
        try:
            count = model._default_manager.count()
        except Exception:
            continue
        try:
            admin_url = reverse(f"admin:{model._meta.app_label}_{model._meta.model_name}_changelist")
        except NoReverseMatch:
            admin_url = ""
        catalog.append({
            "app_label": model._meta.app_label,
            "model_name": model._meta.model_name,
            "label": model._meta.verbose_name_plural,
            "db_table": model._meta.db_table,
            "count": count,
            "admin_url": admin_url,
        })
    return sorted(catalog, key=lambda row: (row["app_label"], row["db_table"]))


def _safe_value(field_name, value):
    if any(part in field_name.lower() for part in SENSITIVE_PARTS):
        return "••••••••" if value else ""
    if value is None:
        return "-"
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


@superuser_required
@require_GET
def index(request):
    runs = TestScenarioRun.objects.select_related("patient", "visit", "device", "created_by")[:20]
    sensor_assignments = list(
        DeviceAssignment.objects
        .select_related("device", "visit", "visit__patient", "visit__queue")
        .filter(
            Q(visit__queue__status=Queue.Status.MONITORING)
            | Q(
                visit__final_severity=Visit.Severity.YELLOW,
                visit__queue__status__in=[
                    Queue.Status.OBSERVATION_MONITORING,
                ],
            ),
            is_active=True,
            device__is_active=True,
        )
        .order_by("device__device_id")
    )
    role_rows = []
    role_labels = dict(StaffProfile.Role.choices)
    for role, capabilities in ROLE_CAPABILITIES.items():
        role_rows.append({
            "role": role,
            "label": role_labels.get(role, role),
            "description": ROLE_DESCRIPTIONS.get(role, ""),
            "capabilities": [CAPABILITY_LABELS[value] for value in sorted(capabilities)],
        })
    return render(request, "system_test/index.html", {
        "runs": runs,
        "models": _model_catalog(),
        "role_rows": role_rows,
        "sensor_assignments": sensor_assignments,
    })


@superuser_required
@require_GET
def database_index(request):
    catalog = _model_catalog()
    grouped_models = []
    for app_label in sorted({item["app_label"] for item in catalog}):
        grouped_models.append({
            "app_label": app_label,
            "models": [item for item in catalog if item["app_label"] == app_label],
        })
    return render(request, "system_test/database_index.html", {
        "models": catalog,
        "grouped_models": grouped_models,
        "table_count": len(catalog),
        "row_count": sum(item["count"] for item in catalog),
    })


def _unique_test_national_id():
    while True:
        candidate = "9" + "".join(str(secrets.randbelow(10)) for _ in range(12))
        if not Patient.objects.filter(national_id=candidate).exists():
            return candidate


@superuser_required
@require_POST
@transaction.atomic
def create_random_registered_patient(request):
    """Create one complete synthetic registration at the waiting-vitals step."""
    first_name, gender = random.choice([
        ("สมชาย", "M"),
        ("อนันต์", "M"),
        ("ธนกฤต", "M"),
        ("กิตติพงษ์", "M"),
        ("มาลี", "F"),
        ("พิมพ์ชนก", "F"),
        ("สุภาวดี", "F"),
        ("นภัสสร", "F"),
    ])
    last_name = random.choice([
        "ใจดี",
        "สุขสวัสดิ์",
        "รุ่งเรือง",
        "แสงทอง",
        "ศรีสุข",
        "บุญช่วย",
        "มั่นคง",
        "วัฒนา",
    ])
    symptom = random.choice([
        "มีไข้ ไอ และอ่อนเพลีย",
        "เวียนศีรษะ คลื่นไส้ รับประทานอาหารได้น้อย",
        "ปวดท้องเป็นพัก ๆ ไม่มีอาเจียน",
        "ปวดศีรษะและนอนไม่หลับ",
        "เจ็บคอ มีน้ำมูก และไอเล็กน้อย",
        "ปวดข้อเข่าหลังเดินเป็นเวลานาน",
        "แน่นหน้าอกเล็กน้อยและใจสั่น",
        "มีผื่นคันบริเวณแขนและลำตัว",
    ])
    chronic_disease, medication = random.choice([
        ("ไม่มี", "ไม่มี"),
        ("ความดันโลหิตสูง", "Amlodipine 5 mg"),
        ("เบาหวานชนิดที่ 2", "Metformin 500 mg"),
        ("ภูมิแพ้", "Cetirizine 10 mg"),
        ("ไขมันในเลือดสูง", "Simvastatin 20 mg"),
    ])
    allergy = random.choice(["ไม่มี", "แพ้ยา Penicillin", "แพ้ยา Sulfa", "แพ้อาหารทะเล"])
    province, district, subdistrict, postal_code = random.choice([
        ("กรุงเทพมหานคร", "บางเขน", "อนุสาวรีย์", "10220"),
        ("นนทบุรี", "เมืองนนทบุรี", "บางกระสอ", "11000"),
        ("ปทุมธานี", "คลองหลวง", "คลองหนึ่ง", "12120"),
        ("สมุทรปราการ", "เมืองสมุทรปราการ", "ปากน้ำ", "10270"),
    ])
    unique_suffix = "".join(str(secrets.randbelow(10)) for _ in range(8))
    years_old = random.randint(18, 82)
    birth_date = date.today() - timedelta(days=(years_old * 365) + random.randint(0, 364))
    height = random.randint(150, 182)
    weight = random.randint(48, 92)

    patient = Patient.objects.create(
        first_name=first_name,
        last_name=last_name,
        national_id=_unique_test_national_id(),
        gender=gender,
        birth_date=birth_date,
        nationality="ไทย",
        phone=f"09{unique_suffix}",
        email=f"test.{unique_suffix}@example.test",
        address_area=random.choice(["AREA1", "AREA2", "AREA3"]),
        address=f"{random.randint(1, 199)}/{random.randint(1, 99)} ถนนทดสอบ",
        province=province,
        district=district,
        subdistrict=subdistrict,
        postal_code=postal_code,
        blood_type=random.choice(["A", "B", "AB", "O"]),
        chronic_diseases=chronic_disease,
        allergies=allergy,
        medications=medication,
        height_cm=height,
        weight_kg=weight,
        bp_sys=random.randint(105, 145),
        bp_dia=random.randint(65, 95),
        emergency_name=f"{random.choice(['วิชัย', 'อรทัย', 'สมพร', 'วาสนา'])} {last_name}",
        emergency_relationship=random.choice(["SPOUSE", "CHILD", "SIBLING", "RELATIVE"]),
        emergency_phone=f"08{''.join(str(secrets.randbelow(10)) for _ in range(8))}",
        note="[SYSTEM TEST] ผู้ป่วยลงทะเบียนด้วยข้อมูลสุ่ม สามารถลบจากหน้า /test",
    )
    visit = Visit.objects.create(patient=patient, note=f"[SYSTEM TEST] {symptom}")
    VitalSign.objects.create(visit=visit)
    queue = Queue.objects.create(visit=visit, status=Queue.Status.WAITING_VITALS, priority=5)
    run = TestScenarioRun.objects.create(
        scenario=TestScenarioRun.Scenario.WAITING,
        patient=patient,
        visit=visit,
        created_by=request.user,
    )

    messages.success(
        request,
        f"เพิ่มผู้ป่วยสุ่ม {patient.first_name} {patient.last_name} · {queue.display_number} "
        f"· อาการ: {symptom} (TEST #{run.id})",
    )
    return redirect("system_test:index")


@superuser_required
@require_POST
def send_sensor_packet(request):
    """Simulate one physical sensor packet through the real /api/iot/vitals/ handler."""
    assignment = get_object_or_404(
        DeviceAssignment.objects.select_related("device", "visit", "visit__patient", "visit__queue"),
        pk=request.POST.get("assignment_id"),
        is_active=True,
        device__is_active=True,
    )
    mode = request.POST.get("mode", "normal")
    presets = {
        "normal": {"heart_rate": 82, "spo2": 98, "temperature": 36.8, "respiratory_rate": 18},
        "warning": {"heart_rate": 112, "spo2": 94, "temperature": 38.2, "respiratory_rate": 27},
        "critical": {"heart_rate": 138, "spo2": 87, "temperature": 39.4, "respiratory_rate": 36},
    }
    values = presets.get(mode)
    if values is None:
        messages.error(request, "รูปแบบค่าจำลองไม่ถูกต้อง")
        return redirect("system_test:index")

    payload = {
        "device_id": assignment.device.device_id,
        "patient_id": assignment.visit.patient.hn or str(assignment.visit.patient_id),
        **values,
    }
    sensor_request = RequestFactory().post(
        "/api/iot/vitals/",
        data=json.dumps(payload),
        content_type="application/json",
        HTTP_X_API_KEY=assignment.device.api_key,
    )
    response = iot_vitals(sensor_request)
    try:
        result = json.loads(response.content.decode("utf-8"))
    except (TypeError, ValueError, UnicodeDecodeError):
        result = {}

    if response.status_code != 200:
        messages.error(
            request,
            f"Sensor simulator ส่งไม่สำเร็จ (HTTP {response.status_code}): "
            f"{result.get('message') or result.get('error') or 'unknown error'}",
        )
        return redirect("system_test:index")

    messages.success(
        request,
        f"Sensor simulator → {assignment.device.device_id} / Visit#{assignment.visit_id}: "
        f"HR {values['heart_rate']}, SpO₂ {values['spo2']}, "
        f"BT {values['temperature']}, RR {values['respiratory_rate']}",
    )
    return redirect("system_test:index")


@superuser_required
@require_POST
@transaction.atomic
def create_scenario(request):
    scenario = request.POST.get("scenario", TestScenarioRun.Scenario.FULL)
    if scenario not in TestScenarioRun.Scenario.values:
        messages.error(request, "รูปแบบการทดสอบไม่ถูกต้อง")
        return redirect("system_test:index")

    suffix = timezone.now().strftime("%H%M%S")
    patient = Patient.objects.create(
        first_name=f"ทดสอบ{suffix}",
        last_name="ระบบจำลอง",
        national_id=_unique_test_national_id(),
        birth_date=date.today() - timedelta(days=35 * 365),
        phone="0900000000",
        note="[SYSTEM TEST] ข้อมูลจำลอง ลบได้จากหน้า /test เท่านั้น",
    )
    visit = Visit.objects.create(patient=patient, note="[SYSTEM TEST] อาการจำลอง")
    run = TestScenarioRun.objects.create(scenario=scenario, patient=patient, visit=visit, created_by=request.user)

    if scenario == TestScenarioRun.Scenario.WAITING:
        VitalSign.objects.create(visit=visit)
        Queue.objects.create(visit=visit, status=Queue.Status.WAITING_VITALS, priority=5)
    elif scenario == TestScenarioRun.Scenario.EMERGENCY:
        vitals = VitalSign.objects.create(visit=visit, rr=36, pr=142, sys_bp=78, dia_bp=48, bt=39.1, o2sat=86, pain_score=9)
        visit.final_severity = Visit.Severity.RED
        visit.triaged_at = visit.confirmed_at = timezone.now()
        visit.save(update_fields=["final_severity", "triaged_at", "confirmed_at"])
        Queue.objects.create(visit=visit, status=Queue.Status.EMERGENCY_TRANSFER, priority=1)
        TriageResult.objects.create(visit=visit, ai_severity=Visit.Severity.RED, nurse_severity=Visit.Severity.RED, ai_reason="[SYSTEM TEST] critical vitals")
        create_critical_alerts_for_visit(visit, vitals, source="system_test")
    else:
        vitals = VitalSign.objects.create(visit=visit, rr=22, pr=96, sys_bp=128, dia_bp=78, bt=37.6, o2sat=96, pain_score=5)
        visit.final_severity = Visit.Severity.YELLOW
        visit.triaged_at = visit.confirmed_at = timezone.now()
        visit.save(update_fields=["final_severity", "triaged_at", "confirmed_at"])
        Queue.objects.create(visit=visit, status=Queue.Status.OBSERVATION_MONITORING, priority=3)
        TriageResult.objects.create(visit=visit, ai_severity=Visit.Severity.YELLOW, nurse_severity=Visit.Severity.YELLOW, ai_reason="[SYSTEM TEST] observation")
        device = Device.objects.create(device_id=f"TESTWATCH{run.id:04d}", api_key=secrets.token_urlsafe(24), is_active=True, last_seen=timezone.now())
        DeviceAssignment.objects.create(device=device, visit=visit, is_active=True)
        run.device = device
        run.save(update_fields=["device"])
        _create_telemetry(run, "normal")

    messages.success(request, f"สร้างข้อมูลทดสอบ #{run.id} สำเร็จ ข้อมูลจริงไม่ได้รับผลกระทบ")
    return redirect("system_test:index")


def _create_telemetry(run, mode):
    presets = {
        "normal": {"bpm": random.randint(70, 94), "o2sat": random.randint(97, 100), "bt": round(random.uniform(36.4, 37.3), 1), "rr": random.randint(14, 20)},
        "warning": {"bpm": random.randint(105, 125), "o2sat": random.randint(92, 95), "bt": round(random.uniform(37.8, 38.7), 1), "rr": random.randint(24, 30)},
        "critical": {"bpm": random.randint(130, 155), "o2sat": random.randint(82, 89), "bt": round(random.uniform(39.0, 40.0), 1), "rr": random.randint(34, 42)},
    }
    values = presets.get(mode, presets["normal"])
    now = timezone.now()
    TelemetryLog.objects.create(visit=run.visit, device=run.device, ts=now, **values)
    vitals, _ = VitalSign.objects.get_or_create(visit=run.visit)
    vitals.pr = values["bpm"]
    vitals.o2sat = values["o2sat"]
    vitals.bt = values["bt"]
    vitals.rr = values["rr"]
    # Wearables intentionally do not overwrite blood pressure.
    vitals.save(update_fields=["pr", "o2sat", "bt", "rr", "updated_at"])
    run.device.last_seen = now
    run.device.save(update_fields=["last_seen"])
    create_critical_alerts_for_visit(run.visit, vitals, source="system_test_iot")
    return values


@superuser_required
@require_POST
@transaction.atomic
def push_telemetry(request, run_id):
    run = get_object_or_404(TestScenarioRun.objects.select_related("patient", "visit", "device"), pk=run_id)
    if not run.patient or not run.visit or not run.device:
        messages.error(request, "สถานการณ์นี้ไม่มีนาฬิกาจำลอง")
        return redirect("system_test:index")
    mode = request.POST.get("mode", "normal")
    if mode not in {"normal", "warning", "critical"}:
        mode = "normal"
    values = _create_telemetry(run, mode)
    messages.success(request, f"ส่งข้อมูลนาฬิกา {mode}: PR {values['bpm']}, SpO₂ {values['o2sat']}, BT {values['bt']}, RR {values['rr']}")
    return redirect("system_test:index")


@superuser_required
@require_POST
@transaction.atomic
def delete_scenario(request, run_id):
    run = get_object_or_404(TestScenarioRun, pk=run_id)
    patient = run.patient
    device = run.device
    run.delete()
    if patient:
        patient.delete()
    if device:
        device.delete()
    messages.success(request, f"ลบข้อมูลจำลอง #{run_id} แล้ว โดยไม่แตะข้อมูลผู้ป่วยจริง")
    return redirect("system_test:index")


@superuser_required
@require_GET
def database_table(request, app_label, model_name):
    model = _allowed_model(app_label, model_name)

    fields = [field for field in model._meta.concrete_fields]
    records = model._default_manager.order_by(f"-{model._meta.pk.name}")
    search_query = request.GET.get("q", "").strip()
    if search_query:
        search_filter = Q()
        searchable_fields = [
            field for field in fields
            if isinstance(field, (models.CharField, models.TextField, models.EmailField))
            and not _is_sensitive_field(field)
        ]
        for field in searchable_fields:
            search_filter |= Q(**{f"{field.name}__icontains": search_query})
        try:
            search_filter |= Q(**{model._meta.pk.name: model._meta.pk.to_python(search_query)})
        except (TypeError, ValueError, ValidationError):
            pass
        if search_filter:
            records = records.filter(search_filter)

    page_size_choices = (25, 50, 100)
    try:
        page_size = int(request.GET.get("page_size", 25))
    except (TypeError, ValueError):
        page_size = 25
    if page_size not in page_size_choices:
        page_size = 25
    paginator = Paginator(records, page_size)
    page_obj = paginator.get_page(request.GET.get("page"))
    rows = [
        {
            "pk": record.pk,
            "values": [_safe_value(field.name, getattr(record, field.attname, None)) for field in fields],
        }
        for record in page_obj.object_list
    ]
    return render(request, "system_test/database_table.html", {
        "model": model,
        "app_label": model._meta.app_label,
        "model_name": model._meta.model_name,
        "db_table": model._meta.db_table,
        "model_label": model._meta.verbose_name_plural,
        "fields": fields,
        "rows": rows,
        "total": model._default_manager.count(),
        "filtered_total": paginator.count,
        "page_obj": page_obj,
        "page_size": page_size,
        "page_size_choices": page_size_choices,
        "search_query": search_query,
        "can_edit": bool(_editable_field_names(model)),
    })


@superuser_required
@transaction.atomic
def database_record_edit(request, app_label, model_name, object_id):
    model = _allowed_model(app_label, model_name)
    editable_fields = _editable_field_names(model)
    if not editable_fields:
        messages.error(request, "ตารางนี้ไม่มีฟิลด์ที่อนุญาตให้แก้ไขจากหน้านี้")
        return redirect("database_table_root", app_label=app_label, model_name=model_name)

    record = get_object_or_404(model._default_manager, pk=object_id)
    form_class = modelform_factory(model, fields=editable_fields)
    form = form_class(request.POST or None, instance=record)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, f"บันทึกข้อมูล {model._meta.verbose_name} #{record.pk} แล้ว")
        return redirect("database_table_root", app_label=app_label, model_name=model_name)

    return render(request, "system_test/database_record_edit.html", {
        "form": form,
        "record": record,
        "model_label": model._meta.verbose_name,
        "db_table": model._meta.db_table,
        "app_label": app_label,
        "model_name": model_name,
    })
