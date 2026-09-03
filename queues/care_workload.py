from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Count, Q
from django.utils import timezone

from .models import NurseCareAssignment, Queue, StaffDuty, StaffProfile, Visit


MAX_PATIENTS_PER_NURSE = 4

CARE_ACTIVE_STATUSES = {
    Queue.Status.WAITING_QUEUE,
    Queue.Status.CALLED,
    Queue.Status.OBSERVATION_MONITORING,
    Queue.Status.REASSESSMENT_REQUIRED,
    Queue.Status.MONITORING,
}

TERMINAL_CARE_STATUSES = {
    Queue.Status.EMERGENCY_TRANSFER,
    Queue.Status.OPD_DONE,
    Queue.Status.FOLLOWUP,
    Queue.Status.DISCHARGED,
    Queue.Status.CANCELLED,
}


@dataclass
class HandoverResult:
    moved: int = 0
    unassigned: int = 0
    skipped: int = 0


def nurse_active_count(nurse) -> int:
    return NurseCareAssignment.objects.filter(nurse=nurse, is_active=True).count()


def nurse_active_assignments(nurse):
    return (
        NurseCareAssignment.objects
        .select_related("visit", "visit__patient", "visit__queue", "nurse")
        .filter(nurse=nurse, is_active=True)
        .order_by("assigned_at")
    )


def available_nurse_duties(*, today=None, exclude_user_ids: Iterable[int] | None = None):
    today = today or timezone.localdate()
    qs = (
        StaffDuty.objects
        .select_related("user", "user__hospital_staff_profile")
        .filter(
            duty_date=today,
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
    )
    if exclude_user_ids:
        qs = qs.exclude(user_id__in=list(exclude_user_ids))
    return qs.order_by(
        "patient_count",
        "user__first_name",
        "user__last_name",
        "user__username",
    )


def nurse_workload_rows(*, today=None, include_unavailable=True):
    """Return nurse workload rows used by personnel and handover screens."""
    today = today or timezone.localdate()
    users = list(
        get_user_model().objects
        .filter(
            is_active=True,
            hospital_staff_profile__role=StaffProfile.Role.NURSE,
        )
        .select_related("hospital_staff_profile")
        .order_by("first_name", "last_name", "username")
    )
    duties = {
        duty.user_id: duty
        for duty in StaffDuty.objects.filter(duty_date=today, user__in=users)
    }
    assignments = list(
        NurseCareAssignment.objects
        .select_related("visit", "visit__patient", "visit__queue")
        .filter(nurse__in=users, is_active=True)
        .order_by("assigned_at")
    )
    by_nurse = {user.id: [] for user in users}
    for assignment in assignments:
        by_nurse.setdefault(assignment.nurse_id, []).append(assignment)

    rows = []
    for user in users:
        duty = duties.get(user.id)
        active_cases = by_nurse.get(user.id, [])
        patient_count = len(active_cases)
        present = bool(duty and duty.is_present)
        available_flag = bool(duty and duty.is_present and duty.is_available)
        has_capacity = patient_count < MAX_PATIENTS_PER_NURSE
        ready = available_flag and has_capacity
        if patient_count >= MAX_PATIENTS_PER_NURSE:
            load_state = "full"
        elif patient_count >= 2:
            load_state = "busy"
        else:
            load_state = "free"
        rows.append({
            "user": user,
            "duty": duty,
            "is_present": present,
            "is_available": available_flag,
            "ready": ready,
            "patient_count": patient_count,
            "remaining_capacity": max(0, MAX_PATIENTS_PER_NURSE - patient_count),
            "has_capacity": has_capacity,
            "load_state": load_state,
            "active_cases": active_cases,
        })

    if not include_unavailable:
        rows = [row for row in rows if row["ready"]]
    rows.sort(key=lambda row: (
        not row["is_present"],
        row["load_state"] == "full",
        row["patient_count"],
        (row["user"].get_full_name() or row["user"].username).casefold(),
    ))
    return rows


def _lock_duty_with_capacity(nurse_id: int):
    duty = (
        StaffDuty.objects
        .select_for_update()
        .select_related("user", "user__hospital_staff_profile")
        .filter(
            user_id=nurse_id,
            duty_date=timezone.localdate(),
            is_present=True,
            is_available=True,
            user__is_active=True,
            user__hospital_staff_profile__role=StaffProfile.Role.NURSE,
        )
        .first()
    )
    if not duty:
        return None, None
    active_count = NurseCareAssignment.objects.filter(nurse_id=nurse_id, is_active=True).count()
    if active_count >= MAX_PATIENTS_PER_NURSE:
        return None, active_count
    return duty, active_count


def choose_nurse_id(*, exclude_user_ids: Iterable[int] | None = None):
    for duty in available_nurse_duties(exclude_user_ids=exclude_user_ids):
        if (duty.patient_count or 0) < MAX_PATIENTS_PER_NURSE:
            return duty.user_id
    return None


def is_visit_assignable(visit: Visit) -> bool:
    queue = getattr(visit, "queue", None)
    if not queue or queue.status not in CARE_ACTIVE_STATUSES:
        return False
    if queue.status in {Queue.Status.WAITING_QUEUE, Queue.Status.CALLED}:
        return visit.final_severity == Visit.Severity.YELLOW
    return True


def end_assignment_for_visit(visit: Visit, *, when=None) -> int:
    when = when or timezone.now()
    return NurseCareAssignment.objects.filter(visit=visit, is_active=True).update(
        is_active=False,
        ended_at=when,
    )


def assign_visit_to_nurse(*, visit: Visit, nurse, assigned_by=None):
    """Assign a visit while enforcing on-duty status and the four-patient cap."""
    if not is_visit_assignable(visit):
        raise ValueError("visit_not_assignable")

    with transaction.atomic():
        current = (
            NurseCareAssignment.objects
            .select_for_update()
            .filter(visit=visit, is_active=True)
            .first()
        )
        if current and current.nurse_id == nurse.id:
            return current, nurse_active_count(nurse)

        duty, active_count = _lock_duty_with_capacity(nurse.id)
        if not duty:
            if active_count is not None and active_count >= MAX_PATIENTS_PER_NURSE:
                raise ValueError("nurse_full")
            raise ValueError("nurse_unavailable")

        if current:
            current.is_active = False
            current.ended_at = timezone.now()
            current.save(update_fields=["is_active", "ended_at"])

        assignment = NurseCareAssignment.objects.create(
            nurse=nurse,
            visit=visit,
            assigned_by=assigned_by,
        )
        return assignment, active_count + 1


def auto_assign_visit(*, visit: Visit, assigned_by=None, exclude_user_ids: Iterable[int] | None = None):
    attempted = set(exclude_user_ids or [])
    while True:
        nurse_id = choose_nurse_id(exclude_user_ids=attempted)
        if not nurse_id:
            raise ValueError("no_nurse_capacity")
        nurse = get_user_model().objects.get(pk=nurse_id)
        try:
            return assign_visit_to_nurse(visit=visit, nurse=nurse, assigned_by=assigned_by)
        except ValueError as exc:
            if str(exc) not in {"nurse_full", "nurse_unavailable"}:
                raise
            attempted.add(nurse_id)


def handover_nurse_cases(*, from_nurse, assigned_by=None, target_nurse=None, release_unassigned=True):
    """Move all active cases away from a nurse, using least-loaded staff by default."""
    result = HandoverResult()
    assignments = list(nurse_active_assignments(from_nurse))

    for old_assignment in assignments:
        visit = old_assignment.visit
        try:
            if target_nurse is not None:
                assign_visit_to_nurse(
                    visit=visit,
                    nurse=target_nurse,
                    assigned_by=assigned_by,
                )
            else:
                auto_assign_visit(
                    visit=visit,
                    assigned_by=assigned_by,
                    exclude_user_ids={from_nurse.id},
                )
            result.moved += 1
        except ValueError:
            if release_unassigned:
                end_assignment_for_visit(visit)
                result.unassigned += 1
            else:
                result.skipped += 1

    return result
