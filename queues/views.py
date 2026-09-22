from datetime import timedelta
import json
import secrets

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import IntegrityError
from django.db.models import Count, OuterRef, Q, Subquery, Value
from django.db.models.functions import Concat
from django.core.paginator import Paginator
from django.http import JsonResponse
from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST, require_GET
import random , string
from django.db import transaction
from django.apps import apps




from ai_triage.services import apply_ai_triage, localize_ai_reason
from accounts.access import is_effective_superuser
from patients.models import Patient
from .forms import DeviceCreateForm, DeviceManagementPairForm, DevicePairingForm, NurseTriageAssessmentForm
from .models import CriticalAlert, NurseCareAssignment, Queue, Visit, Device, DeviceAssignment, TelemetryLog, VitalSign, TriageResult, VisitWorkflowLog
from .triage import EMERGENCY_SEVERITIES, SEVERITY_LEVELS, SEVERITY_PRIORITY
from .training_cases import capture_confirmed_triage_case

QUEUE_VISIBLE_STATUSES = [
    Queue.Status.WAITING_QUEUE,
    Queue.Status.OBSERVATION_MONITORING,
    Queue.Status.REASSESSMENT_REQUIRED,
    Queue.Status.CALLED,
]
QUEUE_ORDERABLE_STATUSES = [
    Queue.Status.WAITING_QUEUE,
    Queue.Status.OBSERVATION_MONITORING,
    Queue.Status.CALLED,
]
QUEUE_CALLABLE_STATUSES = [
    Queue.Status.WAITING_QUEUE,
    Queue.Status.OBSERVATION_MONITORING,
]
REQUIRED_VITAL_FIELDS = ["rr", "pr", "sys_bp", "dia_bp", "bt", "o2sat"]


def mask_api_key(api_key):
    if not api_key:
        return "-"
    if len(api_key) <= 8:
        return f"{api_key[:2]}***{api_key[-2:]}"
    return f"{api_key[:4]}...{api_key[-4:]}"


def has_required_vitals(vitals):
    return bool(vitals and all(getattr(vitals, field) is not None for field in REQUIRED_VITAL_FIELDS))


def unpair_active_wearable(visit):
    """Close active wearable assignments and keep the unpair timestamp as an audit trail."""
    now = timezone.now()
    updated = DeviceAssignment.objects.filter(visit=visit, is_active=True).update(
        is_active=False,
        unpaired_at=now,
    )
    NurseCareAssignment.objects.filter(visit=visit, is_active=True).update(
        is_active=False,
        ended_at=now,
    )
    return updated


def route_after_nurse_confirmation(visit, severity):
    """Apply the nurse-confirmed flow. AI output never calls this function directly."""
    q = visit.queue
    has_active_wearable = DeviceAssignment.objects.filter(visit=visit, is_active=True).exists()

    q.priority = SEVERITY_PRIORITY[severity]
    if severity in EMERGENCY_SEVERITIES:
        unpair_active_wearable(visit)
        q.status = Queue.Status.EMERGENCY_TRANSFER
    elif severity == Visit.Severity.YELLOW:
        q.status = Queue.Status.OBSERVATION_MONITORING if has_active_wearable else Queue.Status.WAITING_QUEUE
    else:
        unpair_active_wearable(visit)
        q.status = Queue.Status.WAITING_QUEUE

    q.save(update_fields=["priority", "status"])
    return q


def evaluate_visit_if_vitals_complete(visit):
    vitals = getattr(visit, "vitals", None)
    if not has_required_vitals(vitals):
        return None

    result = apply_ai_triage(visit)
    q = getattr(visit, "queue", None)
    if q and q.status == Queue.Status.WAITING_VITALS:
        q.status = Queue.Status.WAITING_CONFIRMATION
        q.save(update_fields=["status"])
    return result


def create_critical_alerts_for_visit(visit, vitals, source="vitals"):
    checks = [
        (
            CriticalAlert.AlertType.LOW_O2,
            vitals.o2sat,
            lambda value: value is not None and value < 95,
            "SpO2 ต่ำกว่า 95%",
            "< 95",
        ),
        (
            CriticalAlert.AlertType.LOW_BP,
            vitals.sys_bp,
            lambda value: value is not None and value < 90,
            "ความดันตัวบนต่ำกว่า 90 mmHg",
            "< 90",
        ),
        (
            CriticalAlert.AlertType.HIGH_RR,
            vitals.rr,
            lambda value: value is not None and value > 30,
            "อัตราการหายใจสูงกว่า 30 ครั้ง/นาที",
            "> 30",
        ),
        (
            CriticalAlert.AlertType.HIGH_HEART_RATE,
            vitals.pr,
            lambda value: value is not None and value >= 120,
            "ชีพจรสูงตั้งแต่ 120 ครั้ง/นาที",
            ">= 120",
        ),
        (
            CriticalAlert.AlertType.LOW_HEART_RATE,
            vitals.pr,
            lambda value: value is not None and value < 40,
            "ชีพจรต่ำกว่า 40 ครั้ง/นาที",
            "< 40",
        ),
        (
            CriticalAlert.AlertType.HIGH_TEMPERATURE,
            vitals.bt,
            lambda value: value is not None and value >= 39,
            "อุณหภูมิสูงตั้งแต่ 39 °C",
            ">= 39",
        ),
    ]

    created = []
    for alert_type, value, is_critical, message, threshold in checks:
        if not is_critical(value):
            continue
        exists = CriticalAlert.objects.filter(
            visit=visit,
            alert_type=alert_type,
            status__in=CriticalAlert.ACTIVE_STATUSES,
        ).exists()
        if exists:
            continue
        alert = CriticalAlert.objects.create(
            visit=visit,
            alert_type=alert_type,
            severity=Visit.Severity.PINK,
            message=message,
            value=value,
            threshold=threshold,
            source=source,
        )
        created.append(alert)
        VisitWorkflowLog.record(
            visit=visit,
            event_type=VisitWorkflowLog.EventType.CRITICAL_ALERT_CREATED,
            description=f"สร้างสัญญาณเตือน: {message}",
            details={
                "alert_id": alert.id,
                "alert_type": alert.alert_type,
                "severity": alert.severity,
                "value": alert.value,
                "threshold": alert.threshold,
                "source": alert.source,
            },
        )

    # Sensor alerts are handled by the alert workflow. Do not send the patient
    # back through triage/reassessment automatically; a clinician decides the next step.
    return created


# -----------------------------
# QUEUE
# -----------------------------
@login_required
def queue_list(request):
    queue_items = (
        Queue.objects
        .select_related("visit", "visit__patient", "visit__triage_result")
        .prefetch_related("visit__nurse_care_assignments__nurse")
        .filter(status__in=QUEUE_VISIBLE_STATUSES)
        .order_by("priority", "-is_expedited", "visit__confirmed_at", "created_at", "pk")
    )

    search_query = request.GET.get("q", "").strip()
    if search_query:
        if len(search_query.split()) > 1:
            queue_items = queue_items.annotate(
                patient_full_name=Concat(
                    "visit__patient__first_name",
                    Value(" "),
                    "visit__patient__last_name",
                ),
            ).filter(
                Q(patient_full_name__icontains=search_query)
                | Q(visit__patient__hn__iexact=search_query)
                | Q(visit__patient__national_id__iexact=search_query)
                | Q(visit__patient__phone__iexact=search_query)
            )
        else:
            queue_items = queue_items.filter(
                Q(visit__patient__first_name__icontains=search_query)
                | Q(visit__patient__last_name__icontains=search_query)
                | Q(visit__patient__hn__icontains=search_query)
                | Q(visit__patient__national_id__icontains=search_query)
                | Q(visit__patient__phone__icontains=search_query)
            )

    severity_counts = {severity: 0 for severity in SEVERITY_LEVELS}
    for item in queue_items.values("visit__final_severity").annotate(total=Count("id")):
        severity = item["visit__final_severity"]
        if severity in severity_counts:
            severity_counts[severity] = item["total"]

    page_size_choices = (10, 20, 30, 40, 50)
    try:
        page_size = int(request.GET.get("page_size", 10))
    except (TypeError, ValueError):
        page_size = 10
    if page_size not in page_size_choices:
        page_size = 10

    paginator = Paginator(queue_items, page_size)
    q_items = paginator.get_page(request.GET.get("page"))
    pagination_items = [
        item if isinstance(item, int) else None
        for item in paginator.get_elided_page_range(q_items.number, on_each_side=2, on_ends=1)
    ]
    
    return render(request, "queues/queue_list.html", {
        "q_items": q_items,
        "queue_total": paginator.count,
        "page_size": page_size,
        "page_size_choices": page_size_choices,
        "pagination_items": pagination_items,
        "search_query": search_query,
        "severity_counts": severity_counts,
        "red_count": severity_counts["RED"],
        "pink_count": severity_counts["PINK"],
        "yellow_count": severity_counts["YELLOW"],
        "green_count": severity_counts["GREEN"],
        "white_count": severity_counts["WHITE"],
    })


@login_required
@require_POST
@transaction.atomic
def adjust_queue(request, visit_id: int):
    """Allow queue staff to expedite or renumber a queue with a required audit reason."""
    queue_item = get_object_or_404(
        Queue.objects.select_for_update().select_related("visit", "visit__patient"),
        visit_id=visit_id,
        status__in=QUEUE_ORDERABLE_STATUSES,
    )
    reason = request.POST.get("reason", "").strip()
    mode = request.POST.get("queue_mode", "normal")
    requested_number = request.POST.get("queue_number", "").strip().upper()

    if len(reason) < 3:
        messages.error(request, "กรุณาระบุเหตุผลในการปรับคิวอย่างน้อย 3 ตัวอักษร")
        return redirect("queue_list")
    if mode not in {"normal", "expedited"}:
        messages.error(request, "รูปแบบการจัดลำดับคิวไม่ถูกต้อง")
        return redirect("queue_list")

    if requested_number.startswith("Q"):
        requested_number = requested_number[1:]
    if requested_number:
        if not requested_number.isdigit() or not 1 <= int(requested_number) <= 9999:
            messages.error(request, "เลขคิวต้องเป็นตัวเลข 1–9999 เช่น 12 หรือ Q012")
            return redirect("queue_list")
        new_sequence = int(requested_number)
        requested_display = f"Q{new_sequence:03d}"
        has_duplicate = any(
            other.display_number == requested_display
            for other in Queue.objects.filter(status__in=QUEUE_VISIBLE_STATUSES).exclude(pk=queue_item.pk)
        )
        if has_duplicate:
            messages.error(request, f"เลขคิว {requested_display} ถูกใช้งานอยู่ กรุณาเลือกเลขอื่น")
            return redirect("queue_list")
    else:
        new_sequence = None

    old_display = queue_item.display_number
    old_expedited = queue_item.is_expedited
    old_sequence = queue_item.manual_sequence
    new_expedited = mode == "expedited"
    changed_fields = []
    if old_expedited != new_expedited:
        queue_item.is_expedited = new_expedited
        changed_fields.append("is_expedited")
    if old_sequence != new_sequence:
        queue_item.manual_sequence = new_sequence
        changed_fields.append("manual_sequence")

    if not changed_fields:
        messages.info(request, "ไม่มีข้อมูลคิวที่เปลี่ยนแปลง")
        return redirect("queue_list")

    queue_item.save(update_fields=changed_fields)
    if old_expedited != new_expedited:
        VisitWorkflowLog.record(
            visit=queue_item.visit,
            event_type=(
                VisitWorkflowLog.EventType.QUEUE_EXPEDITED
                if new_expedited
                else VisitWorkflowLog.EventType.QUEUE_RESTORED
            ),
            actor=request.user,
            description=reason,
            details={"from": old_expedited, "to": new_expedited},
        )
    if old_sequence != new_sequence:
        VisitWorkflowLog.record(
            visit=queue_item.visit,
            event_type=VisitWorkflowLog.EventType.QUEUE_NUMBER_CHANGED,
            actor=request.user,
            description=f"{old_display} → {queue_item.display_number}: {reason}",
            details={"from": old_display, "to": queue_item.display_number},
        )

    messages.success(request, f"ปรับคิว {old_display} เป็น {queue_item.display_number} และบันทึก Log แล้ว")
    return redirect("queue_list")


@require_GET
def queue_display(request):
    """Privacy-safe waiting-room display; exposes queue numbers, never patient data."""
    q_items = (
        Queue.objects
        .select_related("visit")
        .filter(status__in=QUEUE_VISIBLE_STATUSES)
        .order_by("priority", "-is_expedited", "visit__confirmed_at", "created_at")
    )
    return render(request, "queues/queue_display.html", {"q_items": q_items})


def _vitals_payload(vitals):
    if not vitals:
        return {"hr": None, "o2sat": None, "bt": None, "rr": None, "sys_bp": None, "dia_bp": None}
    return {
        "hr": vitals.pr,
        "o2sat": vitals.o2sat,
        "bt": vitals.bt,
        "rr": vitals.rr,
        "sys_bp": vitals.sys_bp,
        "dia_bp": vitals.dia_bp,
    }


@login_required
def waiting_vitals(request):
    q_items = (
        Queue.objects
        .select_related("visit", "visit__patient", "visit__vitals")
        .filter(status=Queue.Status.WAITING_VITALS)
        .order_by("created_at")
    )

    search_query = request.GET.get("q", "").strip()
    if search_query:
        q_items = q_items.annotate(
            patient_full_name=Concat(
                "visit__patient__first_name",
                Value(" "),
                "visit__patient__last_name",
            ),
        ).filter(
            Q(patient_full_name__icontains=search_query)
            | Q(visit__patient__first_name__icontains=search_query)
            | Q(visit__patient__last_name__icontains=search_query)
            | Q(visit__patient__hn__icontains=search_query)
            | Q(visit__patient__national_id__icontains=search_query)
            | Q(visit__patient__phone__icontains=search_query)
        )

    page_size_choices = (10, 20, 30, 40, 50)
    try:
        page_size = int(request.GET.get("page_size", 10))
    except (TypeError, ValueError):
        page_size = 10
    if page_size not in page_size_choices:
        page_size = 10

    paginator = Paginator(q_items, page_size)
    page = paginator.get_page(request.GET.get("page"))
    pagination_items = [
        item if isinstance(item, int) else None
        for item in paginator.get_elided_page_range(page.number, on_each_side=2, on_ends=1)
    ]
    return render(request, "queues/waiting_vitals.html", {
        "q_items": page,
        "waiting_total": paginator.count,
        "search_query": search_query,
        "page_size": page_size,
        "page_size_choices": page_size_choices,
        "pagination_items": pagination_items,
    })


@login_required
def waiting_confirmation(request):
    q_items = list(
        Queue.objects
        .select_related("visit", "visit__patient", "visit__triage_result", "visit__vitals")
        .filter(status=Queue.Status.WAITING_CONFIRMATION)
        .order_by("visit__triaged_at", "created_at")
    )
    for queue_item in q_items:
        triage_result = getattr(queue_item.visit, "triage_result", None)
        queue_item.ai_reason_display = localize_ai_reason(
            getattr(triage_result, "ai_reason", "")
        )
    return render(request, "queues/waiting_confirmation.html", {
        "q_items": q_items,
        "risk_flag_choices": NurseTriageAssessmentForm.RISK_FLAG_CHOICES,
    })


@login_required
def emergency_transfers(request):
    queryset = (
        Queue.objects
        .select_related("visit", "visit__patient", "visit__triage_result", "visit__vitals")
        .filter(status=Queue.Status.EMERGENCY_TRANSFER)
        .order_by("priority", "visit__confirmed_at", "created_at")
    )
    severity_counts = {
        "red": queryset.filter(visit__final_severity=Visit.Severity.RED).count(),
        "pink": queryset.filter(visit__final_severity=Visit.Severity.PINK).count(),
    }
    paginator = Paginator(queryset, 10)
    page = paginator.get_page(request.GET.get("page"))
    for queue_item in page.object_list:
        triage_result = getattr(queue_item.visit, "triage_result", None)
        queue_item.ai_reason_display = localize_ai_reason(
            getattr(triage_result, "ai_reason", "")
        )
        er_log = (
            queue_item.visit.workflow_logs
            .filter(
                event_type=VisitWorkflowLog.EventType.CRITICAL_ALERT_ESCALATED,
                details__destination="ER",
            )
            .order_by("-created_at")
            .first()
        )
        queue_item.er_transfer_reason = (
            (er_log.details or {}).get("reason", "") if er_log else ""
        )
        queue_item.er_transfer_from_monitoring = bool(er_log)

    return render(request, "queues/emergency_transfers.html", {
        "q_items": page,
        "page_obj": page,
        "emergency_total": paginator.count,
        "red_total": severity_counts["red"],
        "pink_total": severity_counts["pink"],
    })


@login_required
@require_POST
def return_to_waiting_vitals(request, visit_id: int):
    visit = get_object_or_404(Visit.objects.select_related("patient"), id=visit_id)
    q = getattr(visit, "queue", None)

    if q and q.status == Queue.Status.WAITING_CONFIRMATION:
        q.status = Queue.Status.WAITING_VITALS
        q.save(update_fields=["status"])
        messages.info(request, "ส่งกลับไปหน้ารอวัดค่าแล้ว สามารถแก้ vital signs และประเมิน AI ใหม่ได้")

    return redirect("waiting_confirmation")


@login_required
def call_visit(request, visit_id: int):
    visit = get_object_or_404(Visit.objects.select_related("patient"), id=visit_id)
    q = getattr(visit, "queue", None)
    if not q:
        return redirect("queue_list")

    if request.method == "POST":
        room = request.POST.get("exam_room")
        if room not in {"1", "2", "3"}:
            return render(request, "queues/select_exam_room.html", {
                "visit": visit,
                "queue": q,
                "rooms": [1, 2, 3],
                "error": "กรุณาเลือกห้องตรวจ",
            })

        if q.status not in QUEUE_CALLABLE_STATUSES:
            return redirect("queue_list")

        q.status = Queue.Status.CALLED
        q.exam_room = int(room)
        q.save(update_fields=["status", "exam_room"])

        visit.called_at = timezone.now()
        visit.save(update_fields=["called_at"])
        VisitWorkflowLog.record(
            visit=visit,
            event_type=VisitWorkflowLog.EventType.QUEUE_CALLED,
            actor=request.user,
            description=f"เรียกเข้าห้องตรวจ {room}",
            details={"exam_room": int(room), "queue_number": q.display_number},
        )

        # Queue operators own the call action but may not have doctor/OPD
        # permissions. Keep them in the queue workflow after a successful call.
        return redirect("queue_list")

    if q and q.status in QUEUE_CALLABLE_STATUSES:
        return render(request, "queues/select_exam_room.html", {
            "visit": visit,
            "queue": q,
            "rooms": [1, 2, 3],
        })

    return redirect("queue_list")


@login_required
@transaction.atomic
def nurse_triage_assessment(request, visit_id: int):
    visit = get_object_or_404(Visit.objects.select_related("patient"), id=visit_id)
    q = getattr(visit, "queue", None)
    triage_result = getattr(visit, "triage_result", None)

    initial = {}
    vitals = getattr(visit, "vitals", None)
    if vitals:
        initial = {
            "rr": vitals.rr,
            "pr": vitals.pr,
            "sys_bp": vitals.sys_bp,
            "dia_bp": vitals.dia_bp,
            "bt": vitals.bt,
            "o2sat": vitals.o2sat,
            "pain_score": vitals.pain_score,
            "urgent_symptoms": vitals.urgent_symptoms,
            "risk_flags": vitals.risk_flags,
        }
        for field in ["rr", "pr", "sys_bp", "dia_bp", "bt", "o2sat"]:
            if initial[field] is None:
                initial[f"{field}_unmeasured"] = True
    if visit.note:
        initial["symptoms"] = visit.note

    if triage_result:
        for field in [
            "lifesaving_intervention",
            "high_risk_condition",
            "severe_distress",
        ]:
            value = getattr(triage_result, field)
            if value is not None:
                initial[field] = "yes" if value else "no"
        initial["mental_status"] = triage_result.mental_status or ""
        initial["expected_resources"] = triage_result.expected_resources or ""

    ai_result = None

    if request.method == "POST":
        action = request.POST.get("action", "evaluate")
        is_draft = action == "draft"
        form = NurseTriageAssessmentForm(request.POST, is_draft=is_draft)
        if form.is_valid():
            vitals, _ = VitalSign.objects.get_or_create(visit=visit)
            vitals.rr = form.cleaned_data["rr"]
            vitals.pr = form.cleaned_data["pr"]
            vitals.sys_bp = form.cleaned_data["sys_bp"]
            vitals.dia_bp = form.cleaned_data["dia_bp"]
            vitals.bt = form.cleaned_data["bt"]
            vitals.o2sat = form.cleaned_data["o2sat"]
            vitals.pain_score = form.cleaned_data.get("pain_score")
            vitals.urgent_symptoms = form.cleaned_data.get("urgent_symptoms") or []
            vitals.risk_flags = form.cleaned_data.get("risk_flags") or []
            vitals.save()
            VisitWorkflowLog.record(
                visit=visit,
                event_type=VisitWorkflowLog.EventType.VITALS_RECORDED,
                actor=request.user,
                description="บันทึกข้อมูลสัญญาณชีพจากจุดวัดค่า",
                details={
                    "rr": vitals.rr,
                    "pr": vitals.pr,
                    "sys_bp": vitals.sys_bp,
                    "dia_bp": vitals.dia_bp,
                    "bt": vitals.bt,
                    "o2sat": vitals.o2sat,
                    "is_draft": is_draft,
                },
            )
            create_critical_alerts_for_visit(visit, vitals, source="triage")

            visit.note = form.cleaned_data.get("symptoms", "")
            visit.save(update_fields=["note"])

            triage_result, _ = TriageResult.objects.get_or_create(visit=visit)
            for field in [
                "lifesaving_intervention",
                "high_risk_condition",
                "severe_distress",
            ]:
                answer = form.cleaned_data.get(field)
                setattr(triage_result, field, None if not answer else answer == "yes")
            triage_result.mental_status = form.cleaned_data.get("mental_status") or None
            triage_result.altered_mental_status = (
                None
                if not triage_result.mental_status
                else triage_result.mental_status != TriageResult.MentalStatus.ALERT
            )
            triage_result.expected_resources = form.cleaned_data.get("expected_resources") or None
            triage_result.save(update_fields=[
                "lifesaving_intervention",
                "high_risk_condition",
                "altered_mental_status",
                "mental_status",
                "severe_distress",
                "expected_resources",
            ])

            if not is_draft:
                ai_result = apply_ai_triage(visit)
                triage_result = getattr(visit, "triage_result", None)
                if q:
                    q.status = Queue.Status.WAITING_CONFIRMATION
                    q.save(update_fields=["status"])
                    return redirect("waiting_confirmation")
            else:
                ai_result = {"draft": True}
    else:
        form = NurseTriageAssessmentForm(initial=initial)

    return render(request, "queues/nurse_triage_assessment.html", {
        "visit": visit,
        "queue": q,
        "form": form,
        "ai_result": ai_result,
        "triage_result": triage_result,
        "risk_flag_choices": NurseTriageAssessmentForm.RISK_FLAG_CHOICES,
    })


@login_required
@require_POST
@transaction.atomic
def triage_visit(request, visit_id: int):
    visit = get_object_or_404(Visit, id=visit_id)
    new_sev = request.POST.get("severity")
    nurse_note = request.POST.get("nurse_note", "").strip()

    if new_sev not in Visit.Severity.values:
        messages.error(request, "ระดับความเร่งด่วนไม่ถูกต้อง")
        return redirect("waiting_confirmation")

    triage_result, _ = TriageResult.objects.get_or_create(visit=visit)
    if triage_result.ai_severity and new_sev != triage_result.ai_severity and not nurse_note:
        messages.error(request, "กรุณาระบุเหตุผลเพิ่มเติมเมื่อยืนยันระดับต่างจากคำแนะนำของ AI")
        return redirect("waiting_confirmation")

    if request.POST.get("risk_flags_present") == "1":
        allowed_risk_flags = {value for value, _label in NurseTriageAssessmentForm.RISK_FLAG_CHOICES}
        selected_risk_flags = request.POST.getlist("risk_flags")
        if any(value not in allowed_risk_flags for value in selected_risk_flags):
            messages.error(request, "ข้อมูลกลุ่มพิเศษไม่ถูกต้อง")
            return redirect("waiting_confirmation")
        vitals, _ = VitalSign.objects.get_or_create(visit=visit)
        vitals.risk_flags = selected_risk_flags
        vitals.save(update_fields=["risk_flags", "updated_at"])

    visit.final_severity = new_sev
    visit.confirmed_at = timezone.now()
    visit.save(update_fields=["final_severity", "confirmed_at"])
    q = route_after_nurse_confirmation(visit, new_sev)

    triage_result.nurse_severity = new_sev
    triage_result.nurse_note = nurse_note
    triage_result.save(update_fields=["nurse_severity", "nurse_note"])
    capture_confirmed_triage_case(
        visit=visit,
        triage_result=triage_result,
        actor=request.user,
    )
    VisitWorkflowLog.record(
        visit=visit,
        event_type=VisitWorkflowLog.EventType.TRIAGE_CONFIRMED,
        actor=request.user,
        description=nurse_note or f"ยืนยันผลคัดกรองระดับ {new_sev}",
        details={"severity": new_sev, "ai_severity": triage_result.ai_severity},
    )

    if new_sev == Visit.Severity.RED:
        messages.error(request, "RED: ช่วยเหลือทันที ส่งต่อฉุกเฉิน ไม่เข้าคิว OPD และไม่รอสวมนาฬิกา")
        return redirect("waiting_confirmation")
    if new_sev == Visit.Severity.PINK:
        messages.error(request, "PINK: ส่งประเมินฉุกเฉินอย่างรวดเร็ว ไม่เข้าคิว OPD ปกติ")
        return redirect("waiting_confirmation")
    if new_sev == Visit.Severity.YELLOW:
        messages.warning(request, "YELLOW: เข้ากลุ่มเฝ้าระวัง สามารถจับคู่นาฬิกาได้")
    else:
        messages.success(request, f"{new_sev}: เข้าคิว OPD ตามลำดับ ไม่ต้องสวมนาฬิกา")
    return redirect("waiting_confirmation")


@login_required
@require_POST
def send_to_monitoring(request, visit_id: int):
    visit = get_object_or_404(Visit, id=visit_id)
    q = getattr(visit, "queue", None)
    has_device = DeviceAssignment.objects.filter(visit=visit, is_active=True).exists()
    if q and visit.final_severity == Visit.Severity.YELLOW and has_device:
        q.status = Queue.Status.OBSERVATION_MONITORING
        q.save(update_fields=["status"])
    else:
        messages.error(request, "เฉพาะผู้ป่วย YELLOW ที่จับคู่นาฬิกาแล้วเท่านั้นที่เริ่มติดตามได้")
    return redirect("monitor_dashboard")


@login_required
@require_POST
def discharge_visit(request, visit_id: int):
    visit = get_object_or_404(Visit, id=visit_id)
    unpair_active_wearable(visit)
    q = getattr(visit, "queue", None)
    if q:
        q.status = "DISCHARGED"
        q.save(update_fields=["status"])
    return redirect("monitor_dashboard")


@login_required
@require_POST
def cancel_queue(request, visit_id: int):
    visit = get_object_or_404(Visit, id=visit_id)
    q = getattr(visit, "queue", None)
    if q and q.status in {
        Queue.Status.WAITING_VITALS,
        Queue.Status.WAITING_CONFIRMATION,
        Queue.Status.WAITING_QUEUE,
        Queue.Status.OBSERVATION_MONITORING,
        Queue.Status.REASSESSMENT_REQUIRED,
    }:
        unpair_active_wearable(visit)
        q.status = Queue.Status.CANCELLED
        q.save(update_fields=["status"])
    return redirect("queue_list")


@login_required
@require_POST
def update_severity_api(request, visit_id: int):
    """
    API สำหรับ Dashboard เปลี่ยนสี severity
    POST /queues/api/update-severity/<visit_id>/
    Body: {"severity": "RED"|"PINK"|"YELLOW"|"GREEN"|"WHITE"}
    """
    visit = get_object_or_404(Visit, id=visit_id)

    try:
        data = json.loads(request.body.decode("utf-8"))
        new_sev = data.get("severity")

        if new_sev not in SEVERITY_LEVELS:
            return JsonResponse({"ok": False, "error": "Invalid severity"}, status=400)

        visit.final_severity = new_sev
        visit.confirmed_at = timezone.now()
        visit.save(update_fields=["final_severity", "confirmed_at"])

        q = route_after_nurse_confirmation(visit, new_sev)

        triage_result, _ = TriageResult.objects.get_or_create(visit=visit)
        triage_result.nurse_severity = new_sev
        triage_result.save(update_fields=["nurse_severity"])
        capture_confirmed_triage_case(
            visit=visit,
            triage_result=triage_result,
            actor=request.user,
        )
        VisitWorkflowLog.record(
            visit=visit,
            event_type=VisitWorkflowLog.EventType.TRIAGE_CONFIRMED,
            actor=request.user,
            description="ปรับระดับความเร่งด่วนจากหน้าจัดการคิว",
            details={"severity": new_sev},
        )

        return JsonResponse({
            "ok": True,
            # Visit IDs are 64-bit values and exceed JavaScript's safe integer
            # range. Send them as strings so browser links are never rounded.
            "visit_id": str(visit.id),
            "severity": new_sev,
            "queue_status": q.status,
            "wearable_eligible": new_sev == Visit.Severity.YELLOW,
        })

    except Exception as e:
        return JsonResponse({"ok": False, "error": str(e)}, status=400)


# -----------------------------
# IoT API
# -----------------------------
@csrf_exempt
@require_POST
def iot_telemetry(request):
    """
    POST /api/iot/telemetry/
    Headers:
      X-DEVICE-ID
      X-API-KEY
    Body:
      {
        "visit_id": 1,
        "ts": "2025-12-17T08:30:00Z",
        "vitals": {"bpm": 90, "o2sat": 97, "bt": 37.1, "rr": 18}
      }
    """
    device_id = request.headers.get("X-DEVICE-ID")
    api_key = request.headers.get("X-API-KEY")
    if not device_id or not api_key:
        return JsonResponse({"ok": False, "error": "Missing X-DEVICE-ID or X-API-KEY"}, status=401)

    try:
        device = Device.objects.get(device_id=device_id, api_key=api_key, is_active=True)
    except Device.DoesNotExist:
        return JsonResponse({"ok": False, "error": "Invalid device credentials"}, status=403)

    try:
        data = json.loads(request.body.decode("utf-8"))
    except Exception:
        return JsonResponse({"ok": False, "error": "Invalid JSON"}, status=400)

    assignment = (
        DeviceAssignment.objects
        .select_related("visit", "visit__patient")
        .filter(device=device, is_active=True)
        .first()
    )
    if not assignment:
        return JsonResponse({"ok": False, "error": "Device is not paired to an active visit"}, status=409)

    visit = assignment.visit
    q = getattr(visit, "queue", None)
    observation_allowed = bool(
        q
        and visit.final_severity == Visit.Severity.YELLOW
        and q.status in {
            Queue.Status.OBSERVATION_MONITORING,
            Queue.Status.REASSESSMENT_REQUIRED,
            Queue.Status.CALLED,
        }
    )
    inpatient_allowed = bool(q and q.status == Queue.Status.MONITORING)
    if not (observation_allowed or inpatient_allowed):
        return JsonResponse({
            "ok": False,
            "error": "Wearable telemetry is allowed only for observation or inpatient monitoring visits",
        }, status=409)

    posted_visit_id = data.get("visit_id")
    if posted_visit_id:
        try:
            posted_visit_id = int(posted_visit_id)
        except (TypeError, ValueError):
            return JsonResponse({"ok": False, "error": "visit_id must be an integer"}, status=400)
        if posted_visit_id != visit.id:
            return JsonResponse({
                "ok": False,
                "error": "Posted visit_id does not match active device assignment",
            }, status=409)

    vitals = data.get("vitals") or {}
    # parse ts (ถ้าไม่ส่งมา ใช้เวลาปัจจุบัน)
    ts_str = data.get("ts")
    ts = timezone.now()
    if ts_str:
        try:
            ts = timezone.datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
            if timezone.is_naive(ts):
                ts = timezone.make_aware(ts, timezone.utc)
            ts = ts.astimezone(timezone.get_current_timezone())
        except Exception:
            ts = timezone.now()

    # 1) บันทึก log ทุกครั้ง
    log = TelemetryLog.objects.create(
        visit=visit,
        device=device,
        ts=ts,
        bpm=vitals.get("bpm"),
        o2sat=vitals.get("o2sat"),
        bt=vitals.get("bt"),
        rr=vitals.get("rr"),
    )

    # 2) update device last_seen
    device.last_seen = timezone.now()
    device.save(update_fields=["last_seen"])

    # 3) update VitalSign ล่าสุด (อ่านง่ายใน monitor)
    vs, _ = VitalSign.objects.get_or_create(visit=visit)
    if vitals.get("rr") is not None:
        vs.rr = vitals.get("rr")
    if vitals.get("bpm") is not None:
        vs.pr = vitals.get("bpm")  # pr = bpm
    if vitals.get("bt") is not None:
        vs.bt = vitals.get("bt")
    if vitals.get("o2sat") is not None:
        vs.o2sat = vitals.get("o2sat")
    vs.save()
    alerts = create_critical_alerts_for_visit(visit, vs, source="iot")

    triage_result = evaluate_visit_if_vitals_complete(visit)
    queue_status = getattr(getattr(visit, "queue", None), "status", None)

    return JsonResponse({
        "ok": True,
        "log_id": log.id,
        "vitals_complete": has_required_vitals(vs),
        "queue_status": queue_status,
        "critical_alerts": [alert.id for alert in alerts],
        "ai": triage_result,
    })


def _parse_optional_int(value):
    if value in (None, ""):
        return None
    return int(value)


def _find_patient_by_iot_id(patient_id):
    patient_id = str(patient_id).strip()

    patient = Patient.objects.filter(hn=patient_id).first()
    if patient:
        return patient

    patient = Patient.objects.filter(national_id=patient_id).first()
    if patient:
        return patient

    if patient_id.isdigit():
        return Patient.objects.filter(id=int(patient_id)).first()

    return None


@csrf_exempt
@require_POST
def iot_vitals(request):
    """
    POST /api/iot/vitals/
    Header:
      X-API-Key: <device api_key>
    Expected JSON:
      {
        "device_id": "IOT001",
        "heart_rate": 118,
        "spo2": 94,
        "temperature": 38.9,
        "respiratory_rate": 31
      }
    """
    try:
        data = json.loads(request.body.decode("utf-8"))
    except Exception:
        return JsonResponse({"success": False, "message": "Invalid JSON"}, status=400)

    required_fields = ["device_id", "heart_rate", "spo2", "temperature"]
    missing_fields = [field for field in required_fields if data.get(field) in (None, "")]
    if missing_fields:
        return JsonResponse({
            "success": False,
            "message": "Missing required fields",
            "missing_fields": missing_fields,
        }, status=400)

    try:
        device_id = str(data["device_id"]).strip()
        patient_id = str(data.get("patient_id", "")).strip()
        heart_rate = int(data["heart_rate"])
        spo2 = int(data["spo2"])
        temperature = float(data["temperature"])
        respiratory_rate = _parse_optional_int(data.get("respiratory_rate"))
    except (TypeError, ValueError):
        return JsonResponse({"success": False, "message": "Invalid vital sign value"}, status=400)

    api_key = request.headers.get("X-API-Key")
    if not api_key:
        return JsonResponse({"success": False, "message": "Missing X-API-Key"}, status=401)

    try:
        device = Device.objects.get(device_id=device_id)
    except Device.DoesNotExist:
        return JsonResponse({"success": False, "message": "Invalid device credentials"}, status=403)

    if not device.is_active:
        return JsonResponse({"success": False, "message": "Device is not active"}, status=403)

    if device.api_key != api_key:
        return JsonResponse({"success": False, "message": "Invalid device credentials"}, status=403)

    assignment = (
        DeviceAssignment.objects
        .select_related("visit", "visit__patient", "visit__queue")
        .filter(device=device, is_active=True)
        .first()
    )
    if not assignment:
        return JsonResponse({
            "success": False,
            "message": "Device is not paired to an active visit",
        }, status=409)

    visit = assignment.visit
    q = getattr(visit, "queue", None)
    observation_allowed = bool(
        q
        and visit.final_severity == Visit.Severity.YELLOW
        and q.status in {
            Queue.Status.OBSERVATION_MONITORING,
            Queue.Status.REASSESSMENT_REQUIRED,
            Queue.Status.CALLED,
        }
    )
    inpatient_allowed = bool(q and q.status == Queue.Status.MONITORING)
    if not (observation_allowed or inpatient_allowed):
        return JsonResponse({
            "success": False,
            "message": "Wearable vitals are allowed only for observation or inpatient monitoring visits",
        }, status=409)

    patient = visit.patient

    if patient_id:
        posted_patient = _find_patient_by_iot_id(patient_id)
        if not posted_patient:
            return JsonResponse({"success": False, "message": "Patient not found"}, status=404)
        if posted_patient.id != patient.id:
            return JsonResponse({
                "success": False,
                "message": "Posted patient_id does not match active device assignment",
            }, status=409)

    patient_identifier = patient_id or patient.hn or patient.national_id or str(patient.id)

    device.last_seen = timezone.now()
    device.save(update_fields=["last_seen"])

    telemetry_log = TelemetryLog.objects.create(
        visit=visit,
        device=device,
        bpm=heart_rate,
        o2sat=spo2,
        bt=temperature,
        rr=respiratory_rate,
    )

    vs, _ = VitalSign.objects.get_or_create(visit=visit)
    vs.pr = heart_rate
    vs.o2sat = spo2
    vs.bt = temperature
    if respiratory_rate is not None:
        vs.rr = respiratory_rate
    vs.save()
    create_critical_alerts_for_visit(visit, vs, source="iot_vitals")
    evaluate_visit_if_vitals_complete(visit)

    return JsonResponse({
        "success": True,
        "message": "Vital signs received successfully",
        # Keep "id" as a compatibility alias for existing device clients.
        # The canonical persisted wearable record is now TelemetryLog.
        "id": telemetry_log.id,
        "telemetry_log_id": telemetry_log.id,
        "visit_id": visit.id,
        "patient_id": patient_identifier,
    })


# -----------------------------
# helpers: ดึง "ล่าสุด" ด้วย Subquery
# -----------------------------
def _visit_queryset_with_latest_vitals():
    latest_vs = VitalSign.objects.filter(visit=OuterRef("pk")).order_by("-updated_at")

    latest_any_log = TelemetryLog.objects.filter(visit=OuterRef("pk")).order_by("-ts")

    return (
        Visit.objects
        .select_related("patient")
        .annotate(
            last_ts=Subquery(latest_vs.values("updated_at")[:1]),
            last_bpm=Subquery(latest_vs.values("pr")[:1]),
            last_o2=Subquery(latest_vs.values("o2sat")[:1]),
            last_bt=Subquery(latest_vs.values("bt")[:1]),
            last_rr=Subquery(latest_vs.values("rr")[:1]),

            last_log_ts=Subquery(latest_any_log.values("ts")[:1]),
            last_device_id=Subquery(latest_any_log.values("device__device_id")[:1]),
        )
    )



def _get_ai_severity(visit):
    # กันพัง: บางที relation อาจชื่อ triage_result หรือ triage
    obj = getattr(visit, "triage_result", None) or getattr(visit, "triage", None)
    return getattr(obj, "ai_severity", None)


def _active_nurse_details_by_visit(visit_ids):
    """Return the active responsible nurse shown on monitoring surfaces."""
    assignments = (
        NurseCareAssignment.objects
        .select_related("nurse", "nurse__hospital_staff_profile")
        .filter(visit_id__in=visit_ids, is_active=True)
    )
    details = {}
    for assignment in assignments:
        nurse = assignment.nurse
        profile = getattr(nurse, "hospital_staff_profile", None)
        details[assignment.visit_id] = {
            "name": nurse.get_full_name() or nurse.username,
            "photo_url": (
                f"/queues/personnel/photo/{profile.id}/"
                if profile and profile.photo
                else None
            ),
        }
    return details


# -----------------------------
# MONITOR (หน้า + API)
# -----------------------------
@login_required
def monitor_dashboard(request):
    return render(request, "queues/monitor_dashboard.html")


@login_required
def device_management(request):
    create_form = DeviceCreateForm()
    pair_form = DeviceManagementPairForm()

    if request.method == "POST":
        action = request.POST.get("action")

        if action == "create_device":
            create_form = DeviceCreateForm(request.POST)
            if create_form.is_valid():
                try:
                    device = create_form.save(commit=False)
                    if not device.api_key:
                        device.api_key = secrets.token_urlsafe(24)
                    device.save()
                    messages.success(request, f"สร้างอุปกรณ์ {device.device_id} สำเร็จ")
                    return redirect("device_management")
                except IntegrityError:
                    messages.error(request, "สร้างอุปกรณ์ไม่สำเร็จ: device_id ซ้ำหรือข้อมูลชนกับฐานข้อมูล")
            else:
                messages.error(request, "กรุณาตรวจสอบข้อมูลสร้างอุปกรณ์")

        elif action == "pair_device":
            pair_form = DeviceManagementPairForm(request.POST)
            if pair_form.is_valid():
                device = pair_form.cleaned_data["device"]
                visit = pair_form.cleaned_data["visit"]
                now = timezone.now()

                with transaction.atomic():
                    DeviceAssignment.objects.filter(device=device, is_active=True).update(
                        is_active=False,
                        unpaired_at=now,
                    )
                    DeviceAssignment.objects.filter(visit=visit, is_active=True).update(
                        is_active=False,
                        unpaired_at=now,
                    )
                    DeviceAssignment.objects.create(device=device, visit=visit, is_active=True)

                    q = getattr(visit, "queue", None)
                    if q and q.status != Queue.Status.MONITORING:
                        q.status = Queue.Status.OBSERVATION_MONITORING
                        q.save(update_fields=["status"])

                messages.success(request, "ผูกอุปกรณ์สำเร็จ")
                return redirect("device_management")
            messages.error(request, "กรุณาเลือกอุปกรณ์และ Visit ให้ถูกต้อง")

        elif action == "toggle_device":
            device = get_object_or_404(Device, id=request.POST.get("device_id"))
            device.is_active = not device.is_active
            device.save(update_fields=["is_active"])
            state = "เปิดใช้งาน" if device.is_active else "ปิดใช้งาน"
            messages.success(request, f"{state} {device.device_id} แล้ว")
            return redirect("device_management")

        elif action == "delete_device":
            device = get_object_or_404(Device, id=request.POST.get("device_id"))
            device_code = device.device_id
            try:
                with transaction.atomic():
                    DeviceAssignment.objects.filter(device=device, is_active=True).update(
                        is_active=False,
                        unpaired_at=timezone.now(),
                    )
                    device.delete()
                messages.success(request, f"Deleted device {device_code}")
            except IntegrityError:
                messages.error(request, f"Cannot delete device {device_code}")
            return redirect("device_management")

        elif action == "unpair_device":
            assignment = get_object_or_404(
                DeviceAssignment.objects.select_related("device", "visit"),
                id=request.POST.get("assignment_id"),
                is_active=True,
            )
            assignment.is_active = False
            assignment.unpaired_at = timezone.now()
            assignment.save(update_fields=["is_active", "unpaired_at"])
            q = getattr(assignment.visit, "queue", None)
            if q and q.status in {Queue.Status.OBSERVATION_MONITORING, Queue.Status.REASSESSMENT_REQUIRED}:
                q.status = Queue.Status.WAITING_QUEUE
                q.save(update_fields=["status"])
            messages.success(request, f"ยกเลิกการผูก {assignment.device.device_id} แล้ว")
            return redirect("device_management")

        else:
            messages.error(request, "คำสั่งไม่ถูกต้อง")

    active_assignments = {
        assignment.device_id: assignment
        for assignment in (
            DeviceAssignment.objects
            .select_related("device", "visit", "visit__patient", "visit__queue")
            .filter(is_active=True)
        )
    }
    devices = Device.objects.order_by("device_id")
    for device in devices:
        device.active_assignment = active_assignments.get(device.id)
        device.masked_api_key = mask_api_key(device.api_key)

    return render(request, "queues/device_management.html", {
        "create_form": create_form,
        "pair_form": pair_form,
        "devices": devices,
    })


@login_required
@transaction.atomic
def device_pairing(request):
    if request.method == "POST":
        form = DevicePairingForm(request.POST)
        if form.is_valid():
            visit = form.cleaned_data["visit"]
            device = form.cleaned_data["device"]
            now = timezone.now()
            DeviceAssignment.objects.filter(device=device, is_active=True).update(
                is_active=False,
                unpaired_at=now,
            )
            DeviceAssignment.objects.filter(visit=visit, is_active=True).update(
                is_active=False,
                unpaired_at=now,
            )
            DeviceAssignment.objects.create(visit=visit, device=device)
            q = visit.queue
            if q.status != Queue.Status.MONITORING:
                q.status = Queue.Status.OBSERVATION_MONITORING
                q.save(update_fields=["status"])
            device.last_seen = timezone.now()
            device.save(update_fields=["last_seen"])
            return redirect("device_pairing")
    else:
        form = DevicePairingForm()

    latest_pairings = (
        DeviceAssignment.objects
        .select_related("visit", "visit__patient", "device")
        .order_by("-paired_at")[:30]
    )
    devices = Device.objects.order_by("device_id")

    return render(request, "queues/device_pairing.html", {
        "form": form,
        "latest_pairings": latest_pairings,
        "devices": devices,
    })


@login_required
def monitor_latest_api(request):
    """
    ส่งข้อมูลล่าสุดให้หน้า monitor (รีเฟรชทุก 5 วิ)
    ONLINE = มี log ภายใน 3 นาที
    """
    offline_after = timezone.now() - timedelta(minutes=3)

    latest_log = (
        TelemetryLog.objects
        .filter(visit=OuterRef("visit"))
        .order_by("-ts")
    )

    q_items = (
        Queue.objects
        .select_related("visit", "visit__patient", "visit__triage_result")
        .filter(status__in=[Queue.Status.OBSERVATION_MONITORING, Queue.Status.REASSESSMENT_REQUIRED])
        .annotate(
            last_log_ts=Subquery(latest_log.values("ts")[:1]),
            last_device_id=Subquery(latest_log.values("device__device_id")[:1]),
            last_bpm=Subquery(latest_log.values("bpm")[:1]),
            last_o2sat=Subquery(latest_log.values("o2sat")[:1]),
            last_bt=Subquery(latest_log.values("bt")[:1]),
            last_rr=Subquery(latest_log.values("rr")[:1]),
        )
        .order_by("priority", "created_at")[:200]
    )

    q_items = list(q_items)
    nurse_by_visit = _active_nurse_details_by_visit([q.visit_id for q in q_items])
    rows = []
    for q in q_items:
        visit = q.visit

        online = bool(q.last_log_ts and q.last_log_ts >= offline_after)

        rows.append({
            # Keep the exact 64-bit identifier in JavaScript clients.
            "visit_id": str(visit.id),
            "name": f"{visit.patient.first_name} {visit.patient.last_name}",
            "severity": visit.final_severity,
            "queue_status": q.status,
            "has_active_alert": CriticalAlert.objects.filter(
                visit=visit,
                status__in=CriticalAlert.ACTIVE_STATUSES,
            ).exists(),
            "ai": _get_ai_severity(visit),
            "device_id": q.last_device_id,
            "online": online,

            "bpm": q.last_bpm,
            "o2sat": q.last_o2sat,
            "bt": q.last_bt,
            "rr": q.last_rr,
            "responsible_nurse": nurse_by_visit.get(visit.id),
            "critical_alerts": list(
                CriticalAlert.objects
                .filter(visit=visit, status__in=CriticalAlert.ACTIVE_STATUSES)
                .values("id", "alert_type", "message", "value", "threshold", "status")
            ),

            "registered_at": visit.registered_at.isoformat() if visit.registered_at else None,
        })

    return JsonResponse({"ok": True, "rows": rows})


@login_required
@require_POST
@transaction.atomic
def acknowledge_alert(request, alert_id: int):
    alert = get_object_or_404(
        CriticalAlert.objects.select_for_update().select_related("visit"),
        id=alert_id,
    )
    queue_status = Queue.objects.filter(visit_id=alert.visit_id).values_list("status", flat=True).first()

    if not is_effective_superuser(request.user):
        is_responsible_nurse = NurseCareAssignment.objects.filter(
            visit=alert.visit,
            nurse=request.user,
            is_active=True,
        ).exists()
        if not is_responsible_nurse:
            return JsonResponse({"ok": False, "message": "Only the responsible nurse can manage this alert"}, status=403)

    if alert.status != CriticalAlert.Status.NEW:
        return JsonResponse({
            "ok": True,
            "alert_id": alert.id,
            "visit_id": alert.visit_id,
            "status": alert.status,
            "queue_status": queue_status,
            "already_acknowledged": True,
        })

    alert.status = CriticalAlert.Status.ACKNOWLEDGED
    alert.acknowledged_at = timezone.now()
    alert.acknowledged_by = request.user
    alert.save(update_fields=["status", "acknowledged_at", "acknowledged_by"])

    VisitWorkflowLog.record(
        visit=alert.visit,
        event_type=VisitWorkflowLog.EventType.CRITICAL_ALERT_ACKNOWLEDGED,
        actor=request.user,
        description=f"รับทราบสัญญาณเตือนและกำลังไปตรวจผู้ป่วย: {alert.message}",
        details={
            "alert_id": alert.id,
            "alert_type": alert.alert_type,
            "severity": alert.severity,
            "value": alert.value,
            "threshold": alert.threshold,
            "source": alert.source,
        },
    )
    return JsonResponse({
        "ok": True,
        "alert_id": alert.id,
        "visit_id": alert.visit_id,
        "status": alert.status,
        "queue_status": queue_status,
        "already_acknowledged": False,
    })


def _alert_manage_allowed(user, alert):
    if is_effective_superuser(user):
        return True
    return NurseCareAssignment.objects.filter(
        visit=alert.visit,
        nurse=user,
        is_active=True,
    ).exists()


def _alert_action_response(alert):
    queue_status = Queue.objects.filter(visit_id=alert.visit_id).values_list("status", flat=True).first()
    return JsonResponse({
        "ok": True,
        "alert_id": alert.id,
        "visit_id": alert.visit_id,
        "status": alert.status,
        "queue_status": queue_status,
    })


@login_required
@require_POST
@transaction.atomic
def start_alert_review(request, alert_id: int):
    alert = get_object_or_404(CriticalAlert.objects.select_for_update().select_related("visit"), id=alert_id)
    if not _alert_manage_allowed(request.user, alert):
        return JsonResponse({"ok": False, "message": "Only the responsible nurse can manage this alert"}, status=403)
    if alert.status == CriticalAlert.Status.NEW:
        return JsonResponse({"ok": False, "message": "Please acknowledge the alert first"}, status=409)
    if alert.status in {CriticalAlert.Status.RESOLVED, CriticalAlert.Status.FALSE_ALARM}:
        return JsonResponse({"ok": False, "message": "This alert is already closed"}, status=409)
    if alert.status == CriticalAlert.Status.IN_REVIEW:
        return _alert_action_response(alert)

    alert.status = CriticalAlert.Status.IN_REVIEW
    alert.save(update_fields=["status"])
    VisitWorkflowLog.record(
        visit=alert.visit,
        event_type=VisitWorkflowLog.EventType.CRITICAL_ALERT_REVIEW_STARTED,
        actor=request.user,
        description=f"เริ่มตรวจผู้ป่วยที่เตียงจากสัญญาณเตือน: {alert.message}",
        details={"alert_id": alert.id, "alert_type": alert.alert_type, "source": alert.source},
    )
    return _alert_action_response(alert)


@login_required
@require_POST
@transaction.atomic
def resolve_alert(request, alert_id: int):
    alert = get_object_or_404(CriticalAlert.objects.select_for_update().select_related("visit"), id=alert_id)
    if not _alert_manage_allowed(request.user, alert):
        return JsonResponse({"ok": False, "message": "Only the responsible nurse can manage this alert"}, status=403)
    if alert.status == CriticalAlert.Status.NEW:
        return JsonResponse({"ok": False, "message": "Please acknowledge the alert first"}, status=409)
    if alert.status == CriticalAlert.Status.RESOLVED:
        return _alert_action_response(alert)

    alert.status = CriticalAlert.Status.RESOLVED
    alert.save(update_fields=["status"])
    VisitWorkflowLog.record(
        visit=alert.visit,
        event_type=VisitWorkflowLog.EventType.CRITICAL_ALERT_RESOLVED,
        actor=request.user,
        description=f"ตรวจแล้วและกลับไปเฝ้าระวังต่อ: {alert.message}",
        details={"alert_id": alert.id, "alert_type": alert.alert_type, "source": alert.source},
    )
    return _alert_action_response(alert)


@login_required
@require_POST
@transaction.atomic
def false_alarm_alert(request, alert_id: int):
    alert = get_object_or_404(CriticalAlert.objects.select_for_update().select_related("visit"), id=alert_id)
    if not _alert_manage_allowed(request.user, alert):
        return JsonResponse({"ok": False, "message": "Only the responsible nurse can manage this alert"}, status=403)
    if alert.status == CriticalAlert.Status.NEW:
        return JsonResponse({"ok": False, "message": "Please acknowledge the alert first"}, status=409)
    if alert.status == CriticalAlert.Status.FALSE_ALARM:
        return _alert_action_response(alert)

    alert.status = CriticalAlert.Status.FALSE_ALARM
    alert.save(update_fields=["status"])
    VisitWorkflowLog.record(
        visit=alert.visit,
        event_type=VisitWorkflowLog.EventType.CRITICAL_ALERT_FALSE_ALARM,
        actor=request.user,
        description=f"ยืนยันว่าเป็น false alarm / artefact: {alert.message}",
        details={"alert_id": alert.id, "alert_type": alert.alert_type, "source": alert.source},
    )
    return _alert_action_response(alert)


@login_required
@require_POST
@transaction.atomic
def escalate_alert(request, alert_id: int):
    alert = get_object_or_404(CriticalAlert.objects.select_for_update().select_related("visit"), id=alert_id)
    if not _alert_manage_allowed(request.user, alert):
        return JsonResponse({"ok": False, "message": "Only the responsible nurse can manage this alert"}, status=403)
    if alert.status == CriticalAlert.Status.NEW:
        return JsonResponse({"ok": False, "message": "Please acknowledge the alert first"}, status=409)
    if alert.status in {CriticalAlert.Status.RESOLVED, CriticalAlert.Status.FALSE_ALARM}:
        return JsonResponse({"ok": False, "message": "This alert is already closed"}, status=409)
    if alert.status == CriticalAlert.Status.ESCALATED:
        return _alert_action_response(alert)

    alert.status = CriticalAlert.Status.ESCALATED
    alert.save(update_fields=["status"])
    VisitWorkflowLog.record(
        visit=alert.visit,
        event_type=VisitWorkflowLog.EventType.CRITICAL_ALERT_ESCALATED,
        actor=request.user,
        description=f"พยาบาลยกระดับการดูแล/แจ้งแพทย์จากสัญญาณเตือน: {alert.message}",
        details={
            "alert_id": alert.id,
            "alert_type": alert.alert_type,
            "source": alert.source,
            "note": request.POST.get("note", "").strip(),
        },
    )
    return _alert_action_response(alert)


@login_required
@require_POST
@transaction.atomic
def transfer_alert_to_er(request, alert_id: int):
    """Human-confirmed transfer from wearable alert review to the ER workflow."""
    alert = get_object_or_404(
        CriticalAlert.objects.select_for_update().select_related("visit", "visit__patient"),
        id=alert_id,
    )
    if not _alert_manage_allowed(request.user, alert):
        return JsonResponse(
            {"ok": False, "message": "Only the responsible nurse can transfer this patient"},
            status=403,
        )

    if alert.status not in {CriticalAlert.Status.IN_REVIEW, CriticalAlert.Status.ESCALATED}:
        return JsonResponse(
            {
                "ok": False,
                "message": "กรุณารับทราบและเริ่มตรวจผู้ป่วยก่อนส่งต่อ ER",
            },
            status=409,
        )

    reason = request.POST.get("reason", "").strip()
    if len(reason) < 3:
        return JsonResponse(
            {"ok": False, "message": "กรุณาระบุเหตุผลในการส่งต่อ ER อย่างน้อย 3 ตัวอักษร"},
            status=400,
        )

    visit = Visit.objects.select_for_update().get(pk=alert.visit_id)
    queue_item = Queue.objects.select_for_update().filter(visit=visit).first()
    if not queue_item:
        return JsonResponse({"ok": False, "message": "ไม่พบคิวของผู้ป่วย"}, status=409)

    if queue_item.status == Queue.Status.EMERGENCY_TRANSFER:
        return JsonResponse({
            "ok": True,
            "alert_id": alert.id,
            "visit_id": alert.visit_id,
            "status": alert.status,
            "queue_status": queue_item.status,
            "already_transferred": True,
        })

    allowed_source_statuses = {
        Queue.Status.OBSERVATION_MONITORING,
        Queue.Status.MONITORING,
        Queue.Status.CALLED,
        Queue.Status.REASSESSMENT_REQUIRED,
    }
    if queue_item.status not in allowed_source_statuses:
        return JsonResponse(
            {"ok": False, "message": "สถานะผู้ป่วยปัจจุบันไม่สามารถส่งต่อ ER จาก Monitoring ได้"},
            status=409,
        )

    previous_severity = visit.final_severity
    target_severity = (
        Visit.Severity.RED
        if previous_severity == Visit.Severity.RED
        else Visit.Severity.PINK
    )

    # The original nurse-confirmed triage remains in TriageResult.
    # Visit.final_severity reflects the current operational severity after
    # a clinician decides to escalate a deteriorating monitored patient.
    if visit.final_severity != target_severity:
        visit.final_severity = target_severity
        visit.save(update_fields=["final_severity"])

    CriticalAlert.objects.filter(
        visit=visit,
        status__in=CriticalAlert.ACTIVE_STATUSES,
    ).update(status=CriticalAlert.Status.ESCALATED)
    alert.status = CriticalAlert.Status.ESCALATED

    unpair_active_wearable(visit)
    queue_item.status = Queue.Status.EMERGENCY_TRANSFER
    queue_item.priority = SEVERITY_PRIORITY[target_severity]
    queue_item.save(update_fields=["status", "priority"])

    VisitWorkflowLog.record(
        visit=visit,
        event_type=VisitWorkflowLog.EventType.CRITICAL_ALERT_ESCALATED,
        actor=request.user,
        description=f"ส่งต่อ ER หลังตรวจผู้ป่วยจากสัญญาณเตือน: {alert.message}",
        details={
            "alert_id": alert.id,
            "alert_type": alert.alert_type,
            "source": alert.source,
            "destination": "ER",
            "reason": reason,
            "previous_severity": previous_severity,
            "current_severity": target_severity,
        },
    )

    return JsonResponse({
        "ok": True,
        "alert_id": alert.id,
        "visit_id": alert.visit_id,
        "status": CriticalAlert.Status.ESCALATED,
        "queue_status": queue_item.status,
        "severity": target_severity,
        "already_transferred": False,
    })


@login_required
@require_GET
def my_critical_alerts(request):
    """Return unresolved wearable alerts assigned to the signed-in nurse."""
    alerts = (
        CriticalAlert.objects
        .filter(status__in=CriticalAlert.ACTIVE_STATUSES)
        .exclude(visit__queue__status=Queue.Status.EMERGENCY_TRANSFER)
    )
    if not is_effective_superuser(request.user):
        alerts = alerts.filter(
            visit__nurse_care_assignments__nurse=request.user,
            visit__nurse_care_assignments__is_active=True,
        )
    alerts = (
        alerts.select_related("visit", "visit__patient", "visit__queue")
        .distinct()
        .order_by("-created_at")[:20]
    )
    return JsonResponse({
        "ok": True,
        "count": alerts.count(),
        "alerts": [
            {
                "id": alert.id,
                "visit_id": alert.visit_id,
                "queue": alert.visit.queue.display_number,
                "patient": f"{alert.visit.patient.first_name} {alert.visit.patient.last_name}",
                "message": alert.message,
                "value": alert.value,
                "threshold": alert.threshold,
                "status": alert.status,
                "created_at": alert.created_at.isoformat(),
                "actions": {
                    "ack": reverse("acknowledge_alert", args=[alert.id]),
                    "review": reverse("start_alert_review", args=[alert.id]),
                    "resolve": reverse("resolve_alert", args=[alert.id]),
                    "false_alarm": reverse("false_alarm_alert", args=[alert.id]),
                    "escalate": reverse("escalate_alert", args=[alert.id]),
                    "transfer_er": reverse("transfer_alert_to_er", args=[alert.id]),
                },
            }
            for alert in alerts
        ],
    })


@login_required
def monitor_summary_api(request):
    """
    API ให้หน้า dashboard / monitor
    เรียงตาม: RED → YELLOW → GREEN → มาก่อนก่อน
    """
    now = timezone.now()

    q_items = (
        Queue.objects
        .select_related("visit", "visit__patient")
        .filter(status__in=[Queue.Status.OBSERVATION_MONITORING, Queue.Status.REASSESSMENT_REQUIRED])
        .order_by("priority", "visit__confirmed_at", "created_at")[:200]
    )

    q_items = list(q_items)
    visit_ids = [q.visit_id for q in q_items]
    nurse_by_visit = _active_nurse_details_by_visit(visit_ids)

    visits = {
        v.id: v
        for v in _visit_queryset_with_latest_vitals()
        .filter(id__in=visit_ids)
    }

    items = []
    for q in q_items:
        v = visits.get(q.visit_id)
        if not v:
            continue

        online = False
        if v.last_log_ts:
            online = (now - v.last_log_ts).total_seconds() <= 60

        items.append({
            # Keep the exact 64-bit identifier in JavaScript clients.
            "visit_id": str(v.id),
            "queue_number": q.display_number,
            "patient_name": f"{v.patient.first_name} {v.patient.last_name}",
            "severity": v.final_severity,
            "queue_status": q.status,
            "has_active_alert": CriticalAlert.objects.filter(
                visit=v,
                status__in=CriticalAlert.ACTIVE_STATUSES,
            ).exists(),
            "registered_at": v.registered_at.isoformat() if v.registered_at else None,
            "online": online,
            "device_id": v.last_device_id,
            "responsible_nurse": nurse_by_visit.get(v.id),
            "vitals": {
                "bpm": v.last_bpm,
                "o2sat": v.last_o2,
                "bt": v.last_bt,
                "rr": v.last_rr,
            }
        })

    return JsonResponse({
        "ok": True,
        "items": items,
        "server_time": now.isoformat()
    })


@login_required
def monitor_visit_detail(request, visit_id: int):
    visit = get_object_or_404(Visit.objects.select_related("patient", "queue"), pk=visit_id)
    logs = TelemetryLog.objects.filter(visit=visit).select_related("device").order_by("-ts")[:50]

    # ดึงข้อมูล assessment ถ้ามี
    assessment = None
    if hasattr(visit, 'opd_assessment'):
        assessment = visit.opd_assessment

    active_alerts = list(
        CriticalAlert.objects
        .filter(visit=visit, status__in=CriticalAlert.ACTIVE_STATUSES)
        .order_by("-created_at")
    )
    er_transfer_alert = next(
        (
            item for item in active_alerts
            if item.status in {
                CriticalAlert.Status.IN_REVIEW,
                CriticalAlert.Status.ESCALATED,
            }
        ),
        None,
    )
    can_transfer_to_er = bool(
        er_transfer_alert
        and visit.queue.status != Queue.Status.EMERGENCY_TRANSFER
        and _alert_manage_allowed(request.user, er_transfer_alert)
    )

    return render(request, "queues/monitor_visit_detail.html", {
        "visit": visit,
        "logs": logs,
        "assessment": assessment,
        "active_alerts": active_alerts,
        "er_transfer_alert": er_transfer_alert,
        "can_transfer_to_er": can_transfer_to_er,
    })


@login_required
@require_GET
def monitor_sparklines_api(request):
    """
    GET /queues/monitor/api/sparklines/?visit_ids=1,2,3
    return: { ok:true, series: { "1": {"bpm":[...], "o2":[...]}, ... } }
    """
    ids_raw = request.GET.get("visit_ids", "").strip()
    if not ids_raw:
        return JsonResponse({"ok": True, "series": {}})

    try:
        visit_ids = [int(x) for x in ids_raw.split(",") if x.strip().isdigit()]
    except Exception:
        return JsonResponse({"ok": False, "error": "bad visit_ids"}, status=400)

    N = 20  # จำนวนจุดในกราฟเล็ก (ปรับได้)

    logs = (
        TelemetryLog.objects
        .filter(visit_id__in=visit_ids)
        .order_by("visit_id", "-ts")
        .values("visit_id", "bpm", "o2sat")
    )

    series = {}
    for row in logs:
        vid = str(row["visit_id"])
        series.setdefault(vid, {"bpm": [], "o2": []})

        if len(series[vid]["bpm"]) < N and row["bpm"] is not None:
            series[vid]["bpm"].append(row["bpm"])
        if len(series[vid]["o2"]) < N and row["o2sat"] is not None:
            series[vid]["o2"].append(row["o2sat"])

        # ถ้าทั้งสองครบแล้ว จะไม่ต้องเติมเพิ่ม (กันวนเยอะ)
        if len(series[vid]["bpm"]) >= N and len(series[vid]["o2"]) >= N:
            pass

    # reverse ให้เก่า -> ใหม่ (กราฟวิ่งซ้ายไปขวา)
    for vid in series:
        series[vid]["bpm"] = list(reversed(series[vid]["bpm"]))
        series[vid]["o2"]  = list(reversed(series[vid]["o2"]))

    return JsonResponse({"ok": True, "series": series})

@login_required
@require_POST
def demo_create_visit_queue(request):
    """
    POST /demo/create/
    สร้าง Visit+Queue จำลอง 1 รายการ (WAITING)
    """
    # 1) เลือกคนไข้ที่มีอยู่ (ชัวร์สุด)
    patient = Patient.objects.order_by("?").first()
    if not patient:
        return JsonResponse({"ok": False, "error": "No patients in DB. Create a Patient first."}, status=400)

    # 2) สร้าง Visit
    visit = Visit.objects.create(
        patient=patient,
        final_severity=random.choice(list(SEVERITY_LEVELS)),
        confirmed_at=timezone.now(),
    )

    # 3) สร้าง Queue
    priority_map = SEVERITY_PRIORITY
    Queue.objects.create(
        visit=visit,
        status=Queue.Status.WAITING_QUEUE,
        priority=priority_map.get(visit.final_severity, 5),
    )

    return JsonResponse({"ok": True, "visit_id": visit.id})

@login_required
@require_POST
@transaction.atomic
def dashboard_demo_create(request):
    """
    คลิกเดียวสร้าง Patient + Visit + Queue(WAITING) สำหรับเดโม
    """
    # --- 1) สุ่มข้อมูลผู้ป่วย ---
    first_names = ["สมชาย", "สมหญิง", "ธนกฤต", "ณัฐ", "กิตติ", "วราภรณ์", "พิมพ์", "กานต์"]
    last_names  = ["ใจดี", "ศรีสุข", "ทองดี", "มีสุข", "บุญช่วย", "ประเสริฐ", "เจริญพร", "วงศ์ดี"]

    fn = random.choice(first_names)
    ln = random.choice(last_names)

    # สุ่มเลขบัตร/hn แบบง่าย ๆ (ปรับ field ให้ตรงของจริง)
    cid = "".join(random.choice(string.digits) for _ in range(13))
    hn  = "HN" + "".join(random.choice(string.digits) for _ in range(6))

    # --- 2) หาโมเดล Patient ของจริง ---
    Patient = apps.get_model("patients", "Patient")  # ถ้า app/model ไม่ใช่ชื่อนี้ให้แก้ตรงนี้

    # ถ้าในโมเดล Patient ไม่มี field บางตัว ให้ลบออกให้ตรงของเธอ
    patient = Patient.objects.create(
        first_name=fn,
        last_name=ln,
        national_id=cid,
        hn=hn,
    )

    # --- 3) สร้าง Visit ---
    sev_choices = list(SEVERITY_LEVELS)
    sev = random.choices(sev_choices, weights=[1, 2, 3, 5, 5], k=1)[0]

    visit = Visit.objects.create(
        patient=patient,
        final_severity=sev,
        triaged_at=timezone.now(),  # ถ้าไม่อยากให้เหมือนคัดกรองแล้ว ลบบรรทัดนี้ได้
        confirmed_at=timezone.now(),
    )

    # --- 4) สร้าง Queue (WAITING) ---
    priority_map = SEVERITY_PRIORITY
    q = Queue.objects.create(
        visit=visit,
        status=Queue.Status.WAITING_QUEUE,
        priority=priority_map.get(sev, 5),
    )

    return JsonResponse({
        "ok": True,
        "patient_id": patient.id,
        "visit_id": visit.id,
        "queue_id": q.id,
        "severity": sev,
    })

