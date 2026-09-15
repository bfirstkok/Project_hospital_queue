import json
import random
import secrets
from datetime import date, timedelta

from django.apps import apps
from django.contrib import messages
from django.db import transaction
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
    IoTVital,
    Queue,
    TelemetryLog,
    TriageResult,
    Visit,
    VitalSign,
)
from queues.views import create_critical_alerts_for_visit

from .models import TestScenarioRun


VISIBLE_APPS = {"accounts", "admin", "auth", "dashboard", "opd", "patients", "queues", "sessions", "system_test"}
SENSITIVE_PARTS = ("password", "secret", "token", "api_key", "pin_hash", "code_hash")


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
        "scenario_choices": TestScenarioRun.Scenario.choices,
    })


@superuser_required
@require_GET
def database_index(request):
    return render(request, "system_test/database_index.html", {"models": _model_catalog()})


def _unique_test_national_id():
    while True:
        candidate = "9" + "".join(str(secrets.randbelow(10)) for _ in range(12))
        if not Patient.objects.filter(national_id=candidate).exists():
            return candidate


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
    IoTVital.objects.create(
        device_identifier=run.device.device_id,
        patient_identifier=run.patient.hn,
        device_db_id=run.device_id,
        patient_db_id=run.patient_id,
        heart_rate=values["bpm"],
        spo2=values["o2sat"],
        temperature=values["bt"],
        respiratory_rate=values["rr"],
    )
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
    try:
        model = apps.get_model(app_label, model_name)
    except LookupError as exc:
        raise Http404("ไม่พบตาราง") from exc
    if model._meta.auto_created or model._meta.app_label not in VISIBLE_APPS:
        raise Http404("ตารางนี้ไม่อนุญาตให้เปิดดู")

    fields = [field for field in model._meta.concrete_fields]
    records = model._default_manager.order_by(f"-{model._meta.pk.name}")[:100]
    rows = [
        [_safe_value(field.name, getattr(record, field.attname, None)) for field in fields]
        for record in records
    ]
    return render(request, "system_test/database_table.html", {
        "model": model,
        "db_table": model._meta.db_table,
        "model_label": model._meta.verbose_name_plural,
        "fields": fields,
        "rows": rows,
        "total": model._default_manager.count(),
    })
