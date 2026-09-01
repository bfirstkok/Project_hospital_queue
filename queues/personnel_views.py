from datetime import timedelta

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_http_methods

from .models import DeviceAssignment, NurseCareAssignment, Queue, StaffDuty, Visit


ONLINE_WINDOW = timedelta(minutes=5)
CARE_QUEUE_STATUSES = {
    Queue.Status.OBSERVATION_MONITORING,
    Queue.Status.REASSESSMENT_REQUIRED,
    Queue.Status.MONITORING,
}


def _eligible_device_assignments():
    """Wearable patients eligible for nurse responsibility assignment."""
    return (
        DeviceAssignment.objects
        .select_related("device", "visit", "visit__patient", "visit__queue")
        .filter(
            is_active=True,
            device__is_active=True,
            visit__queue__status__in=CARE_QUEUE_STATUSES,
        )
        .order_by("visit__queue__priority", "paired_at")
    )


def _is_online(duty, now):
    return bool(
        duty
        and duty.is_present
        and duty.last_seen_at
        and duty.last_seen_at >= now - ONLINE_WINDOW
    )


@login_required
@require_GET
def staff_heartbeat(request):
    """Lightweight request used by open staff pages to keep online status fresh."""
    return JsonResponse({"online": True, "at": timezone.now().isoformat()})


@login_required
@require_http_methods(["GET", "POST"])
def personnel_dashboard(request):
    now = timezone.now()
    today = timezone.localdate(now)

    if request.method == "POST":
        action = request.POST.get("action", "")

        if action == "set_attendance":
            staff_user = get_object_or_404(
                get_user_model(),
                pk=request.POST.get("user_id"),
                is_active=True,
            )
            is_present = request.POST.get("is_present") == "1"
            duty, _ = StaffDuty.objects.get_or_create(
                user=staff_user,
                duty_date=today,
                defaults={"checked_in_at": now, "last_seen_at": None},
            )
            duty.is_present = is_present
            if is_present:
                duty.checked_in_at = now
                duty.checked_out_at = None
            else:
                duty.checked_out_at = now
            duty.save(update_fields=["is_present", "checked_in_at", "checked_out_at"])
            messages.success(
                request,
                f"อัปเดตสถานะ {staff_user.get_full_name() or staff_user.username} แล้ว",
            )

        elif action == "assign_patient":
            nurse = get_object_or_404(
                get_user_model(),
                pk=request.POST.get("nurse_id"),
                is_active=True,
            )
            duty = StaffDuty.objects.filter(user=nurse, duty_date=today).first()
            if not _is_online(duty, now):
                messages.error(request, "เลือกมอบหมายได้เฉพาะพยาบาลที่กำลังออนไลน์และขึ้นเวรวันนี้")
                return redirect("personnel_dashboard")

            visit_id = request.POST.get("visit_id")
            device_assignment = _eligible_device_assignments().filter(visit_id=visit_id).first()
            if not device_assignment:
                messages.error(request, "ผู้ป่วยต้องสวมอุปกรณ์และอยู่ในกลุ่มเฝ้าระวังหรือกลุ่มนอนรักษา")
                return redirect("personnel_dashboard")

            with transaction.atomic():
                NurseCareAssignment.objects.filter(
                    visit=device_assignment.visit,
                    is_active=True,
                ).update(is_active=False, ended_at=now)
                NurseCareAssignment.objects.create(
                    nurse=nurse,
                    visit=device_assignment.visit,
                    assigned_by=request.user,
                )
            messages.success(
                request,
                f"มอบหมาย {device_assignment.visit.patient} ให้ {nurse.get_full_name() or nurse.username} ดูแลแล้ว",
            )

        elif action == "end_assignment":
            care_assignment = get_object_or_404(
                NurseCareAssignment,
                pk=request.POST.get("assignment_id"),
                is_active=True,
            )
            care_assignment.is_active = False
            care_assignment.ended_at = now
            care_assignment.save(update_fields=["is_active", "ended_at"])
            messages.success(request, "ยุติการมอบหมายผู้ป่วยแล้ว")

        else:
            messages.error(request, "คำสั่งไม่ถูกต้อง")

        return redirect("personnel_dashboard")

    users = list(get_user_model().objects.filter(is_active=True).order_by("first_name", "username"))
    duties = {
        duty.user_id: duty
        for duty in StaffDuty.objects.filter(duty_date=today, user__in=users)
    }
    staff_rows = []
    online_nurses = []
    for staff_user in users:
        duty = duties.get(staff_user.id)
        online = _is_online(duty, now)
        row = {
            "user": staff_user,
            "name": staff_user.get_full_name() or staff_user.username,
            "duty": duty,
            "is_present": bool(duty and duty.is_present),
            "is_online": online,
        }
        staff_rows.append(row)
        if online:
            online_nurses.append(row)

    active_care = {
        item.visit_id: item
        for item in (
            NurseCareAssignment.objects
            .select_related("nurse")
            .filter(is_active=True)
        )
    }
    patient_rows = []
    for device_assignment in _eligible_device_assignments():
        visit = device_assignment.visit
        q = visit.queue
        care_assignment = active_care.get(visit.id)
        assigned_duty = duties.get(care_assignment.nurse_id) if care_assignment else None
        patient_rows.append({
            "visit": visit,
            "patient": visit.patient,
            "queue": q,
            "device": device_assignment.device,
            "care_type": (
                "ผู้ป่วยนอนรักษา/ติดตามหลังตรวจ"
                if q.status == Queue.Status.MONITORING
                else "ผู้ป่วยกลุ่มเฝ้าระวัง"
            ),
            "care_assignment": care_assignment,
            "assigned_nurse_online": _is_online(assigned_duty, now),
        })

    return render(request, "queues/personnel_dashboard.html", {
        "today": today,
        "staff_rows": staff_rows,
        "online_nurses": online_nurses,
        "patient_rows": patient_rows,
        "present_count": sum(row["is_present"] for row in staff_rows),
        "online_count": len(online_nurses),
        "assigned_count": sum(bool(row["care_assignment"]) for row in patient_rows),
    })
