from datetime import date, timedelta

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.db import IntegrityError
from django.db.models import Count
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from accounts.access import Capability, has_capability

from .models import NurseCareAssignment, ShiftSchedule, StaffDuty, StaffProfile


THAI_WEEKDAYS = ("จันทร์", "อังคาร", "พุธ", "พฤหัสบดี", "ศุกร์", "เสาร์", "อาทิตย์")
DEFAULT_DUTY_SUGGESTIONS = (
    "คัดกรองผู้ป่วย",
    "เฝ้าระวังผู้ป่วย",
    "ประจำห้องตรวจ 1",
    "ประจำห้องตรวจ 2",
    "ประจำห้องตรวจ 3",
    "ประสานงานและจัดคิว",
    "ห้องฉุกเฉิน",
    "จุดวัดสัญญาณชีพ",
    "ดูแลอุปกรณ์/นาฬิกา",
    "สำรอง/สนับสนุนหน่วย",
)


def _manager_forbidden(request):
    profile = getattr(request.user, "hospital_staff_profile", None)
    return render(
        request,
        "403.html",
        {
            "required_capability": Capability.MANAGE_PERSONNEL,
            "current_role": getattr(profile, "role", "ADMIN" if request.user.is_superuser else "STAFF"),
        },
        status=403,
    )


def _selected_date(request):
    value = request.GET.get("day") or request.GET.get("week", "")
    try:
        return date.fromisoformat(value) if value else timezone.localdate()
    except ValueError:
        return timezone.localdate()


def _posted_return_day(request):
    return request.POST.get("day") or request.POST.get("week") or timezone.localdate().isoformat()


@require_http_methods(["GET", "POST"])
def shift_schedule(request):
    """Daily roster with an explicit duty/responsibility for each planned shift.

    ShiftSchedule.note is intentionally used as the duty label so existing roster data remains
    compatible and no schema migration is needed. The UI treats it as "หน้าที่ประจำเวร".
    """
    can_manage = has_capability(request.user, Capability.MANAGE_PERSONNEL)
    user_model = get_user_model()

    if request.method == "POST":
        if not can_manage:
            return _manager_forbidden(request)

        action = request.POST.get("action")
        return_day = _posted_return_day(request)

        if action == "delete_shift":
            shift = get_object_or_404(ShiftSchedule, pk=request.POST.get("shift_id"))
            shift.delete()
            messages.success(request, "ลบเวรที่วางไว้แล้ว")
            return redirect(f"{request.path}?day={return_day}")

        if action != "save_shift":
            messages.error(request, "คำสั่งไม่ถูกต้อง")
            return redirect(f"{request.path}?day={return_day}")

        try:
            shift_date = date.fromisoformat(request.POST.get("shift_date", ""))
            start_time = timezone.datetime.strptime(request.POST.get("start_time", ""), "%H:%M").time()
            end_time = timezone.datetime.strptime(request.POST.get("end_time", ""), "%H:%M").time()
        except (TypeError, ValueError):
            messages.error(request, "วันที่หรือเวลาเวรไม่ถูกต้อง")
            return redirect(f"{request.path}?day={return_day}")

        if start_time == end_time:
            messages.error(request, "เวลาเริ่มและสิ้นสุดเวรต้องไม่เท่ากัน")
            return redirect(f"{request.path}?day={return_day}")

        staff_user = get_object_or_404(
            user_model.objects.select_related("hospital_staff_profile"),
            pk=request.POST.get("user_id"),
            is_active=True,
            hospital_staff_profile__isnull=False,
        )

        valid_statuses = {value for value, _label in ShiftSchedule.Status.choices}
        status = request.POST.get("status", ShiftSchedule.Status.SCHEDULED)
        if status not in valid_statuses:
            status = ShiftSchedule.Status.SCHEDULED

        # Backward-compatible: older forms/tests may still submit `note`.
        duty_assignment = (
            request.POST.get("duty_assignment", "").strip()
            or request.POST.get("note", "").strip()
        )[:200]
        if status == ShiftSchedule.Status.SCHEDULED and len(duty_assignment) < 2:
            messages.error(request, "กรุณากำหนดหน้าที่ประจำเวร เช่น คัดกรองผู้ป่วย หรือ ประจำห้องตรวจ 1")
            return redirect(f"{request.path}?day={return_day}")

        shift_id = request.POST.get("shift_id")
        shift = get_object_or_404(ShiftSchedule, pk=shift_id) if shift_id else ShiftSchedule()
        shift.user = staff_user
        shift.shift_date = shift_date
        shift.start_time = start_time
        shift.end_time = end_time
        shift.status = status
        shift.note = duty_assignment
        shift.created_by = shift.created_by or request.user
        try:
            shift.save()
        except IntegrityError:
            messages.error(request, "บุคลากรคนนี้มีเวรที่เริ่มเวลาเดียวกันอยู่แล้ว")
        else:
            messages.success(request, f"บันทึกเวรและหน้าที่ของ {staff_user.get_full_name() or staff_user.username} แล้ว")
        return redirect(f"{request.path}?day={return_day}")

    selected = _selected_date(request)
    week_start = selected - timedelta(days=selected.weekday())
    week_end = week_start + timedelta(days=6)
    previous_day = selected - timedelta(days=1)
    next_day = selected + timedelta(days=1)

    users = list(
        user_model.objects.filter(is_active=True, hospital_staff_profile__isnull=False)
        .exclude(is_superuser=True)
        .select_related("hospital_staff_profile")
        .order_by("hospital_staff_profile__role", "first_name", "username")
    )
    schedules = list(
        ShiftSchedule.objects.filter(shift_date=selected)
        .select_related("user", "user__hospital_staff_profile")
        .order_by("start_time", "user__hospital_staff_profile__role", "user__first_name", "user__username")
    )

    duties = {
        (duty.user_id, duty.duty_date): duty
        for duty in StaffDuty.objects.filter(duty_date=selected, user__in=users)
    }
    active_case_counts = dict(
        NurseCareAssignment.objects.filter(is_active=True)
        .values("nurse_id")
        .annotate(total=Count("id"))
        .values_list("nurse_id", "total")
    )
    for shift in schedules:
        shift.actual_duty = duties.get((shift.user_id, shift.shift_date))
        shift.active_case_count = active_case_counts.get(shift.user_id, 0)
        shift.is_current_user = shift.user_id == request.user.id

    week_count_by_date = dict(
        ShiftSchedule.objects.filter(shift_date__range=(week_start, week_end))
        .values("shift_date")
        .annotate(total=Count("id"))
        .values_list("shift_date", "total")
    )
    week_days = []
    for offset in range(7):
        day = week_start + timedelta(days=offset)
        week_days.append({
            "date": day,
            "label": THAI_WEEKDAYS[offset],
            "is_selected": day == selected,
            "is_today": day == timezone.localdate(),
            "shift_count": week_count_by_date.get(day, 0),
        })

    role_counts = list(
        ShiftSchedule.objects.filter(shift_date=selected)
        .values("user__hospital_staff_profile__role")
        .annotate(total=Count("id"))
        .order_by("user__hospital_staff_profile__role")
    )
    role_labels = dict(StaffProfile.Role.choices)
    for row in role_counts:
        row["label"] = role_labels.get(row["user__hospital_staff_profile__role"], "บุคลากร")

    today = timezone.localdate()
    today_planned = {}
    for shift in (
        ShiftSchedule.objects.filter(shift_date=today, status=ShiftSchedule.Status.SCHEDULED)
        .select_related("user")
        .order_by("start_time")
    ):
        today_planned.setdefault(shift.user_id, shift)

    on_duty_now = []
    for duty in (
        StaffDuty.objects.filter(duty_date=today, is_present=True, user__in=users)
        .select_related("user", "user__hospital_staff_profile")
    ):
        planned = today_planned.get(duty.user_id)
        on_duty_now.append({
            "user": duty.user,
            "profile": duty.user.hospital_staff_profile,
            "case_count": active_case_counts.get(duty.user_id, 0),
            "planned_duty": planned.note if planned and planned.note else "ยังไม่ได้กำหนดหน้าที่",
            "is_current_user": duty.user_id == request.user.id,
        })

    return render(request, "queues/shift_schedule_roles.html", {
        "can_manage": can_manage,
        "users": users,
        "selected_day": selected,
        "selected_day_label": THAI_WEEKDAYS[selected.weekday()],
        "selected_shifts": schedules,
        "week_days": week_days,
        "role_counts": role_counts,
        "on_duty_now": on_duty_now,
        "week_start": week_start,
        "week_end": week_end,
        "previous_day": previous_day,
        "next_day": next_day,
        "shift_statuses": ShiftSchedule.Status.choices,
        "duty_suggestions": DEFAULT_DUTY_SUGGESTIONS,
    })
