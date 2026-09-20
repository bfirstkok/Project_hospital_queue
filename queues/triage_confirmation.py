from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from ai_triage.services import localize_ai_reason

from . import views as legacy_views
from .care_workload import (
    MAX_PATIENTS_PER_NURSE,
    assign_visit_to_nurse,
    auto_assign_visit,
    nurse_workload_rows,
)
from .forms import NurseTriageAssessmentForm
from .models import Device, DeviceAssignment, Queue, Visit


@login_required
def waiting_confirmation(request):
    """Confirmation page with workload-aware nurse recommendation."""
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

    raw_rows = [
        row for row in nurse_workload_rows()
        if row["is_present"] and row["is_available"]
    ]
    nurse_rows = []
    recommended_set = False
    for source in raw_rows:
        row = dict(source)
        row["recommended"] = bool(row["has_capacity"] and not recommended_set)
        if row["recommended"]:
            recommended_set = True
        nurse_rows.append(row)

    recommended_row = next((row for row in nurse_rows if row["recommended"]), None)
    available_devices = list(
        Device.objects
        .filter(is_active=True)
        .exclude(assignments__is_active=True)
        .order_by("device_id")
        .distinct()
    )
    return render(request, "queues/waiting_confirmation_with_nurse.html", {
        "q_items": q_items,
        "risk_flag_choices": NurseTriageAssessmentForm.RISK_FLAG_CHOICES,
        "nurse_rows": nurse_rows,
        "recommended_nurse_id": recommended_row["user"].id if recommended_row else None,
        "has_assignable_nurse": recommended_row is not None,
        "max_patients_per_nurse": MAX_PATIENTS_PER_NURSE,
        "available_devices": available_devices,
        "has_available_device": bool(available_devices),
    })


@login_required
@require_POST
@transaction.atomic
def triage_visit(request, visit_id: int):
    """Require workload-safe nurse ownership for confirmations from the YELLOW UI."""
    selected_severity = request.POST.get("severity")
    require_nurse_assignment = request.POST.get("yellow_assignment_required") == "1"

    # Legacy/internal callers are intentionally preserved. The actual waiting
    # confirmation UI always sends yellow_assignment_required=1.
    if selected_severity != Visit.Severity.YELLOW or not require_nurse_assignment:
        return legacy_views.triage_visit(request, visit_id)

    requested_nurse_id = request.POST.get("nurse_id") or None
    requested_device_id = request.POST.get("device_id") or None
    requested_nurse = None

    # The real YELLOW confirmation UI treats nurse + monitoring device as one
    # atomic handoff. Legacy/internal callers do not send yellow_assignment_required.
    if not requested_device_id or not str(requested_device_id).isdigit():
        messages.error(request, "กรุณาเลือกอุปกรณ์เฝ้าระวังสำหรับผู้ป่วยสีเหลือง")
        return redirect("waiting_confirmation")

    # Lock the Visit row directly. Do not join the reverse OneToOne queue here:
    # PostgreSQL rejects SELECT ... FOR UPDATE on the nullable side of the
    # outer join that select_related("queue") generates.
    visit_lock = get_object_or_404(
        Visit.objects.select_for_update(),
        id=visit_id,
    )
    queue_lock = get_object_or_404(
        Queue.objects.select_for_update(),
        visit=visit_lock,
    )
    requested_device = (
        Device.objects.select_for_update()
        .filter(pk=requested_device_id, is_active=True)
        .first()
    )
    if requested_device is None:
        messages.error(request, "ไม่พบอุปกรณ์ที่เลือกหรืออุปกรณ์ถูกปิดใช้งาน กรุณาเลือกใหม่")
        return redirect("waiting_confirmation")
    if DeviceAssignment.objects.filter(device=requested_device, is_active=True).exists():
        messages.error(request, f"อุปกรณ์ {requested_device.device_id} ถูกผูกกับผู้ป่วยอื่นแล้ว กรุณาเลือกอุปกรณ์ใหม่")
        return redirect("waiting_confirmation")
    if DeviceAssignment.objects.filter(visit=visit_lock, is_active=True).exists():
        messages.error(request, "ผู้ป่วยรายนี้มีอุปกรณ์ที่กำลังใช้งานอยู่แล้ว")
        return redirect("waiting_confirmation")

    if requested_nurse_id:
        if not str(requested_nurse_id).isdigit():
            messages.error(request, "ข้อมูลพยาบาลที่เลือกไม่ถูกต้อง")
            return redirect("waiting_confirmation")
        requested_nurse = get_user_model().objects.filter(pk=requested_nurse_id, is_active=True).first()
        if requested_nurse is None:
            messages.error(request, "ไม่พบพยาบาลที่เลือก กรุณาเลือกใหม่")
            return redirect("waiting_confirmation")

    # Run the existing triage validation/routing first, but keep it inside this
    # outer transaction so a capacity race can roll the whole confirmation back.
    response = legacy_views.triage_visit(request, visit_id)
    visit = get_object_or_404(Visit.objects.select_related("patient"), id=visit_id)
    queue_item = queue_lock
    queue_item.refresh_from_db()
    triage_completed = (
        visit.final_severity == Visit.Severity.YELLOW
        and visit.confirmed_at is not None
        and queue_item is not None
        and queue_item.status != Queue.Status.WAITING_CONFIRMATION
    )
    if not triage_completed:
        return response

    try:
        if requested_nurse is not None:
            assignment, patient_count = assign_visit_to_nurse(
                visit=visit,
                nurse=requested_nurse,
                assigned_by=request.user,
                allow_pre_monitoring=True,
            )
        else:
            assignment, patient_count = auto_assign_visit(
                visit=visit,
                assigned_by=request.user,
                allow_pre_monitoring=True,
            )
    except ValueError as exc:
        transaction.set_rollback(True)
        code = str(exc)
        if code == "nurse_full":
            message = f"พยาบาลที่เลือกดูแลครบ {MAX_PATIENTS_PER_NURSE} คนแล้ว กรุณาเลือกคนอื่น"
        elif code == "nurse_unavailable":
            message = "พยาบาลที่เลือกไม่ได้ขึ้นเวรหรือปิดสถานะพร้อมรับผู้ป่วยแล้ว กรุณาเลือกใหม่"
        else:
            message = f"ยังไม่มีพยาบาลที่ว่างรับผู้ป่วยได้ (สูงสุด {MAX_PATIENTS_PER_NURSE} คนต่อพยาบาล)"
        messages.error(request, message)
        return redirect("waiting_confirmation")

    DeviceAssignment.objects.create(
        device=requested_device,
        visit=visit,
        is_active=True,
    )
    if queue_item.status != Queue.Status.OBSERVATION_MONITORING:
        queue_item.status = Queue.Status.OBSERVATION_MONITORING
        queue_item.save(update_fields=["status"])

    nurse = assignment.nurse
    messages.success(
        request,
        f"ยืนยันสีเหลืองแล้ว · {nurse.get_full_name() or nurse.username} รับผิดชอบ "
        f"(ดูแล {patient_count}/{MAX_PATIENTS_PER_NURSE} คน) · "
        f"ผูกอุปกรณ์ {requested_device.device_id} และเริ่มเฝ้าระวังแล้ว",
    )
    return response
