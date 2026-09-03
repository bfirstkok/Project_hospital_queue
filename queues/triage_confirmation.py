from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from ai_triage.services import localize_ai_reason

from . import views as legacy_views
from .forms import NurseTriageAssessmentForm
from .models import NurseCareAssignment, Queue, StaffDuty, StaffProfile, Visit


MAX_PATIENTS_PER_NURSE = 4


def _nurse_duties_with_load():
    """Return today's available nurses ordered by the lightest active workload."""
    return (
        StaffDuty.objects
        .select_related("user", "user__hospital_staff_profile")
        .filter(
            duty_date=timezone.localdate(),
            is_present=True,
            is_available=True,
            user__is_active=True,
            user__hospital_staff_profile__role=StaffProfile.Role.NURSE,
        )
        .annotate(
            patient_count=Count(
                "user__patient_care_assignments",
                filter=Q(user__patient_care_assignments__is_active=True),
                distinct=True,
            )
        )
        .order_by(
            "patient_count",
            "user__first_name",
            "user__last_name",
            "user__username",
        )
    )


def _nurse_rows():
    rows = []
    recommended_set = False
    for duty in _nurse_duties_with_load():
        count = duty.patient_count or 0
        has_capacity = count < MAX_PATIENTS_PER_NURSE
        recommended = has_capacity and not recommended_set
        if recommended:
            recommended_set = True
        rows.append({
            "user": duty.user,
            "patient_count": count,
            "remaining_capacity": max(0, MAX_PATIENTS_PER_NURSE - count),
            "has_capacity": has_capacity,
            "recommended": recommended,
        })
    return rows


def _lock_assignable_duty(nurse_id=None):
    """Lock and return an on-duty nurse with room for another patient."""
    candidates = _nurse_duties_with_load()
    if nurse_id:
        candidates = candidates.filter(user_id=nurse_id)

    for candidate in candidates:
        duty = (
            StaffDuty.objects
            .select_for_update()
            .select_related("user", "user__hospital_staff_profile")
            .filter(
                pk=candidate.pk,
                duty_date=timezone.localdate(),
                is_present=True,
                is_available=True,
                user__is_active=True,
                user__hospital_staff_profile__role=StaffProfile.Role.NURSE,
            )
            .first()
        )
        if not duty:
            continue

        active_count = NurseCareAssignment.objects.filter(
            nurse=duty.user,
            is_active=True,
        ).count()
        if active_count < MAX_PATIENTS_PER_NURSE:
            return duty, active_count

    return None, None


@login_required
def waiting_confirmation(request):
    """Confirmation page with nurse workload and automatic recommendation."""
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

    nurse_rows = _nurse_rows()
    recommended_row = next((row for row in nurse_rows if row["recommended"]), None)
    return render(request, "queues/waiting_confirmation_with_nurse.html", {
        "q_items": q_items,
        "risk_flag_choices": NurseTriageAssessmentForm.RISK_FLAG_CHOICES,
        "nurse_rows": nurse_rows,
        "recommended_nurse_id": recommended_row["user"].id if recommended_row else None,
        "has_assignable_nurse": recommended_row is not None,
        "max_patients_per_nurse": MAX_PATIENTS_PER_NURSE,
    })


@login_required
@require_POST
@transaction.atomic
def triage_visit(request, visit_id: int):
    """Assign YELLOW patients to an on-duty nurse with a maximum load of four."""
    selected_severity = request.POST.get("severity")
    require_nurse_assignment = request.POST.get("yellow_assignment_required") == "1"

    # Keep legacy/internal callers working. The waiting-confirmation UI always
    # sends yellow_assignment_required=1, so real YELLOW confirmations from that
    # page use workload-aware nurse assignment.
    if selected_severity != Visit.Severity.YELLOW or not require_nurse_assignment:
        return legacy_views.triage_visit(request, visit_id)

    requested_nurse_id = request.POST.get("nurse_id") or None
    duty, active_count = _lock_assignable_duty(requested_nurse_id)

    # If the browser did not send a nurse id, automatically choose the nurse
    # with the smallest active workload. This is also a safe server-side fallback.
    if duty is None and not requested_nurse_id:
        duty, active_count = _lock_assignable_duty()

    if duty is None:
        if requested_nurse_id:
            messages.error(
                request,
                f"พยาบาลที่เลือกไม่พร้อมรับผู้ป่วยหรือดูแลครบ {MAX_PATIENTS_PER_NURSE} คนแล้ว กรุณาเลือกคนอื่น",
            )
        else:
            messages.error(
                request,
                f"ยังไม่มีพยาบาลที่ว่างรับผู้ป่วยได้ (กำหนดสูงสุด {MAX_PATIENTS_PER_NURSE} คนต่อพยาบาล)",
            )
        return redirect("waiting_confirmation")

    nurse = duty.user

    # Preserve all existing triage validation/routing behavior. The legacy view
    # runs inside this outer transaction, so the triage result and assignment
    # are committed together.
    response = legacy_views.triage_visit(request, visit_id)

    visit = get_object_or_404(Visit.objects.select_related("patient"), id=visit_id)
    queue_item = getattr(visit, "queue", None)
    triage_completed = (
        visit.final_severity == Visit.Severity.YELLOW
        and visit.confirmed_at is not None
        and queue_item is not None
        and queue_item.status != Queue.Status.WAITING_CONFIRMATION
    )
    if not triage_completed:
        return response

    current_assignment = (
        NurseCareAssignment.objects
        .select_for_update()
        .filter(visit=visit, is_active=True)
        .first()
    )
    if current_assignment and current_assignment.nurse_id != nurse.id:
        current_assignment.is_active = False
        current_assignment.ended_at = timezone.now()
        current_assignment.save(update_fields=["is_active", "ended_at"])
        current_assignment = None

    if current_assignment is None:
        # Recheck while the duty row is locked. This prevents two concurrent
        # confirmations from pushing the same nurse above the four-patient cap.
        active_count = NurseCareAssignment.objects.filter(
            nurse=nurse,
            is_active=True,
        ).count()
        if active_count >= MAX_PATIENTS_PER_NURSE:
            transaction.set_rollback(True)
            messages.error(
                request,
                f"{nurse.get_full_name() or nurse.username} เพิ่งมีผู้ป่วยครบ {MAX_PATIENTS_PER_NURSE} คน กรุณายืนยันใหม่เพื่อให้ระบบเลือกพยาบาลคนถัดไป",
            )
            return redirect("waiting_confirmation")

        NurseCareAssignment.objects.create(
            nurse=nurse,
            visit=visit,
            assigned_by=request.user,
        )
        active_count += 1

    messages.success(
        request,
        f"มอบหมาย {nurse.get_full_name() or nurse.username} เป็นพยาบาลผู้รับผิดชอบแล้ว (ดูแล {active_count}/{MAX_PATIENTS_PER_NURSE} คน)",
    )
    return response
