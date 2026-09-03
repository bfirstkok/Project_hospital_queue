from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from ai_triage.services import localize_ai_reason

from . import views as legacy_views
from .forms import NurseTriageAssessmentForm
from .models import NurseCareAssignment, Queue, StaffDuty, StaffProfile, Visit


def _available_nurse_duties():
    """Return nurses who are on duty today and accepting patient assignments."""
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
        .order_by("user__first_name", "user__last_name", "user__username")
    )


@login_required
def waiting_confirmation(request):
    """Confirmation page with the current list of assignable nurses."""
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

    available_nurses = [duty.user for duty in _available_nurse_duties()]
    return render(request, "queues/waiting_confirmation_with_nurse.html", {
        "q_items": q_items,
        "risk_flag_choices": NurseTriageAssessmentForm.RISK_FLAG_CHOICES,
        "available_nurses": available_nurses,
    })


@login_required
@require_POST
@transaction.atomic
def triage_visit(request, visit_id: int):
    """Require a responsible on-duty nurse for YELLOW confirmations from the confirmation UI."""
    selected_severity = request.POST.get("severity")
    require_nurse_assignment = request.POST.get("yellow_assignment_required") == "1"

    # Keep legacy/internal callers working. The waiting-confirmation UI always
    # sends yellow_assignment_required=1, so real YELLOW confirmations from that
    # page still cannot proceed without choosing a responsible nurse.
    if selected_severity != Visit.Severity.YELLOW or not require_nurse_assignment:
        return legacy_views.triage_visit(request, visit_id)

    nurse_id = request.POST.get("nurse_id")
    if not nurse_id:
        messages.error(request, "ผู้ป่วยสีเหลืองต้องเลือกพยาบาลผู้รับผิดชอบก่อนยืนยัน")
        return redirect("waiting_confirmation")

    nurse = get_object_or_404(
        get_user_model().objects.select_related("hospital_staff_profile"),
        pk=nurse_id,
        is_active=True,
        hospital_staff_profile__role=StaffProfile.Role.NURSE,
    )
    duty = (
        StaffDuty.objects
        .select_for_update()
        .filter(
            user=nurse,
            duty_date=timezone.localdate(),
            is_present=True,
            is_available=True,
        )
        .first()
    )
    if not duty:
        messages.error(request, "พยาบาลที่เลือกไม่ได้ขึ้นเวรหรือไม่พร้อมรับผู้ป่วย กรุณาเลือกใหม่")
        return redirect("waiting_confirmation")

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
        NurseCareAssignment.objects.create(
            nurse=nurse,
            visit=visit,
            assigned_by=request.user,
        )

    messages.success(
        request,
        f"มอบหมาย {nurse.get_full_name() or nurse.username} เป็นพยาบาลผู้รับผิดชอบผู้ป่วยสีเหลืองแล้ว",
    )
    return response
