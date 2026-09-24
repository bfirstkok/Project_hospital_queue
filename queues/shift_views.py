from collections import defaultdict
from datetime import date, timedelta

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.db import IntegrityError
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from accounts.access import Capability, has_capability

from .models import NurseCareAssignment, ShiftSchedule, StaffDuty, StaffProfile


THAI_WEEKDAYS = ("จันทร์", "อังคาร", "พุธ", "พฤหัสบดี", "ศุกร์", "เสาร์", "อาทิตย์")
ADMIN_ROLE = "ADMIN"
ADMIN_ROLE_LABEL = "ผู้ดูแลระบบ"
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
        return_role = (request.POST.get("return_role") or "").strip()
        valid_return_roles = {ADMIN_ROLE, *StaffProfile.Role.values}
        if return_role not in valid_return_roles:
            return_role = ""
        return_url = f"{request.path}?day={return_day}"
        if return_role:
            return_url += f"&role={return_role}"

        if action == "delete_shift":
            shift = get_object_or_404(ShiftSchedule, pk=request.POST.get("shift_id"))
            shift.delete()
            messages.success(request, "ลบเวรที่วางไว้แล้ว")
            return redirect(return_url)

        if action != "save_shift":
            messages.error(request, "คำสั่งไม่ถูกต้อง")
            return redirect(return_url)

        try:
            shift_date = date.fromisoformat(request.POST.get("shift_date", ""))
            start_time = timezone.datetime.strptime(request.POST.get("start_time", ""), "%H:%M").time()
            end_time = timezone.datetime.strptime(request.POST.get("end_time", ""), "%H:%M").time()
        except (TypeError, ValueError):
            messages.error(request, "วันที่หรือเวลาเวรไม่ถูกต้อง")
            return redirect(return_url)

        if start_time == end_time:
            messages.error(request, "เวลาเริ่มและสิ้นสุดเวรต้องไม่เท่ากัน")
            return redirect(return_url)

        staff_user = get_object_or_404(
            user_model.objects.select_related("hospital_staff_profile"),
            pk=request.POST.get("user_id"),
            is_active=True,
        )
        if (
            not staff_user.is_superuser
            and getattr(staff_user, "hospital_staff_profile", None) is None
        ):
            messages.error(request, "บัญชีนี้ไม่ใช่บุคลากรหรือผู้ดูแลระบบที่จัดเวรได้")
            return redirect(return_url)

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
            return redirect(return_url)

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
        return redirect(return_url)

    selected = _selected_date(request)
    week_start = selected - timedelta(days=selected.weekday())
    week_end = week_start + timedelta(days=6)
    previous_day = selected - timedelta(days=1)
    next_day = selected + timedelta(days=1)
    previous_week = week_start - timedelta(days=7)
    next_week = week_start + timedelta(days=7)

    role_filter = (request.GET.get("role") or "").strip()
    schedule_role_choices = [(ADMIN_ROLE, ADMIN_ROLE_LABEL), *StaffProfile.Role.choices]
    valid_roles = {value for value, _label in schedule_role_choices}
    if role_filter not in valid_roles:
        role_filter = ""
    search_query = (request.GET.get("q") or "").strip()[:80]

    user_qs = (
        user_model.objects.filter(is_active=True, hospital_staff_profile__isnull=False)
        .exclude(is_superuser=True)
        .select_related("hospital_staff_profile")
        .order_by("hospital_staff_profile__role", "first_name", "last_name", "username")
    )
    users = list(user_qs)
    admin_users = list(
        user_model.objects.filter(is_active=True, is_superuser=True)
        .select_related("hospital_staff_profile")
        .order_by("first_name", "last_name", "username")
    )
    schedulable_users = admin_users + users

    def matches_search(person):
        if not search_query:
            return True
        needle = search_query.casefold()
        return needle in " ".join(
            filter(None, [person.first_name, person.last_name, person.username])
        ).casefold()

    def user_role_key(person):
        if person.is_superuser:
            return ADMIN_ROLE
        profile = getattr(person, "hospital_staff_profile", None)
        return profile.role if profile else ""

    def user_role_label(person):
        if person.is_superuser:
            return ADMIN_ROLE_LABEL
        profile = getattr(person, "hospital_staff_profile", None)
        return profile.get_role_display() if profile else "บุคลากร"

    if role_filter == ADMIN_ROLE:
        board_users = [person for person in admin_users if matches_search(person)]
    elif role_filter:
        board_users = [
            person for person in users
            if person.hospital_staff_profile.role == role_filter and matches_search(person)
        ]
    else:
        board_users = [
            person for person in schedulable_users if matches_search(person)
        ]

    schedules = list(
        ShiftSchedule.objects.filter(shift_date=selected, user__in=board_users)
        .select_related("user", "user__hospital_staff_profile")
        .order_by("start_time", "user__first_name", "user__username")
    )

    all_week_schedules = list(
        ShiftSchedule.objects.filter(
            shift_date__range=(week_start, week_end),
            user__is_superuser=False,
            user__hospital_staff_profile__isnull=False,
        )
        .select_related("user", "user__hospital_staff_profile")
        .order_by(
            "user__hospital_staff_profile__role",
            "shift_date",
            "start_time",
            "user__first_name",
            "user__username",
        )
    )

    admin_week_schedules = list(
        ShiftSchedule.objects.filter(
            shift_date__range=(week_start, week_end),
            user__is_superuser=True,
        )
        .select_related("user", "user__hospital_staff_profile")
        .order_by("shift_date", "start_time", "user__first_name", "user__username")
    )

    week_schedules = list(
        ShiftSchedule.objects.filter(
            shift_date__range=(week_start, week_end),
            user__in=board_users,
        )
        .select_related("user", "user__hospital_staff_profile")
        .order_by(
            "user__hospital_staff_profile__role",
            "user__first_name",
            "user__username",
            "shift_date",
            "start_time",
        )
    )

    duties = {
        (duty.user_id, duty.duty_date): duty
        for duty in StaffDuty.objects.filter(duty_date=selected, user__in=schedulable_users)
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
    week_dates = []
    for offset in range(7):
        day = week_start + timedelta(days=offset)
        week_dates.append(day)
        week_days.append({
            "date": day,
            "label": THAI_WEEKDAYS[offset],
            "is_selected": day == selected,
            "is_today": day == timezone.localdate(),
            "shift_count": week_count_by_date.get(day, 0),
        })

    def shift_ui_class(shift):
        if shift.status == ShiftSchedule.Status.LEAVE:
            return "leave"
        if shift.status == ShiftSchedule.Status.CANCELLED:
            return "cancelled"
        if shift.start_time.hour == 0:
            return "night"
        if shift.start_time.hour == 8:
            return "morning"
        if shift.start_time.hour == 16:
            return "evening"
        return "custom"

    schedule_map = defaultdict(list)
    for shift in week_schedules:
        shift.ui_class = shift_ui_class(shift)
        shift.is_current_user = shift.user_id == request.user.id
        schedule_map[(shift.user_id, shift.shift_date)].append(shift)

    board_rows = []
    role_sections_map = {}
    role_section_order = []
    for person in board_users:
        profile = getattr(person, "hospital_staff_profile", None)
        person_role = user_role_key(person)
        person_role_label = user_role_label(person)
        cells = [
            {
                "date": day,
                "shifts": schedule_map.get((person.id, day), []),
                "is_today": day == timezone.localdate(),
                "is_selected": day == selected,
            }
            for day in week_dates
        ]
        row = {
            "user": person,
            "profile": profile,
            "role": person_role,
            "role_label": person_role_label,
            "cells": cells,
            "is_current_user": person.id == request.user.id,
            "initials": (
                ((person.first_name or person.username)[:1])
                + ((person.last_name or "")[:1])
            ).upper(),
        }
        if person_role not in role_sections_map:
            role_sections_map[person_role] = {
                "role": person_role,
                "label": person_role_label,
                "rows": [],
            }
            role_section_order.append(person_role)
        role_sections_map[person_role]["rows"].append(row)
        board_rows.append(row)
    role_sections = [role_sections_map[role] for role in role_section_order]

    selected_shift_summary = {
        "night": sum(
            1 for shift in schedules
            if shift.status == ShiftSchedule.Status.SCHEDULED and shift.start_time.hour == 0
        ),
        "morning": sum(
            1 for shift in schedules
            if shift.status == ShiftSchedule.Status.SCHEDULED and shift.start_time.hour == 8
        ),
        "evening": sum(
            1 for shift in schedules
            if shift.status == ShiftSchedule.Status.SCHEDULED and shift.start_time.hour == 16
        ),
        "leave": sum(1 for shift in schedules if shift.status == ShiftSchedule.Status.LEAVE),
    }

    # Compact overview: one row per role instead of showing all 90 personnel at once.
    active_role_counts = defaultdict(int)
    for person in users:
        active_role_counts[person.hospital_staff_profile.role] += 1

    role_day_summary = defaultdict(lambda: {
        "night": 0,
        "morning": 0,
        "evening": 0,
        "leave": 0,
        "total": 0,
    })
    for shift in all_week_schedules:
        role = user_role_key(shift.user)
        cell = role_day_summary[(role, shift.shift_date)]
        cell["total"] += 1
        if shift.status == ShiftSchedule.Status.LEAVE:
            cell["leave"] += 1
        elif shift.status == ShiftSchedule.Status.SCHEDULED:
            if shift.start_time.hour == 0:
                cell["night"] += 1
            elif shift.start_time.hour == 8:
                cell["morning"] += 1
            elif shift.start_time.hour == 16:
                cell["evening"] += 1

    overview_role_rows = []
    for role, label in StaffProfile.Role.choices:
        overview_role_rows.append({
            "role": role,
            "label": label,
            "people_count": active_role_counts.get(role, 0),
            "cells": [
                {
                    "date": day,
                    **role_day_summary[(role, day)],
                    "is_today": day == timezone.localdate(),
                    "is_selected": day == selected,
                }
                for day in week_dates
            ],
        })

    detailed_mode = bool(role_filter or search_query)

    # Timetable layout used by the main roster screen:
    # rows are weekdays, columns are the three hospital shifts.
    timetable_shift_defs = (
        ("night", "เวรดึก", "00:00–08:00", 0),
        ("morning", "เวรเช้า", "08:00–16:00", 8),
        ("evening", "เวรบ่าย", "16:00–00:00", 16),
    )
    timetable_source = week_schedules if detailed_mode else all_week_schedules
    timetable_bucket = defaultdict(list)
    for shift in timetable_source:
        if shift.status == ShiftSchedule.Status.CANCELLED:
            continue
        start_hour = shift.start_time.hour
        shift_key = next(
            (key for key, _label, _time_label, hour in timetable_shift_defs if hour == start_hour),
            None,
        )
        if shift_key:
            timetable_bucket[(shift.shift_date, shift_key)].append(shift)

    timetable_rows = []
    for day_info in week_days:
        cells = []
        for shift_key, shift_label, time_label, _hour in timetable_shift_defs:
            cell_shifts = timetable_bucket[(day_info["date"], shift_key)]
            scheduled_shifts = [
                shift for shift in cell_shifts
                if shift.status == ShiftSchedule.Status.SCHEDULED
            ]
            leave_count = sum(
                1 for shift in cell_shifts if shift.status == ShiftSchedule.Status.LEAVE
            )

            role_summary_map = defaultdict(list)
            for shift in scheduled_shifts:
                role_summary_map[user_role_key(shift.user)].append(shift)

            role_summaries = []
            for role, label in schedule_role_choices:
                role_shifts = role_summary_map.get(role, [])
                if not role_shifts:
                    continue
                role_summaries.append({
                    "role": role,
                    "label": label,
                    "count": len(role_shifts),
                    "names": [
                        shift.user.get_full_name() or shift.user.username
                        for shift in role_shifts
                    ],
                })

            cells.append({
                "key": shift_key,
                "label": shift_label,
                "time_label": time_label,
                "count": len(scheduled_shifts),
                "leave_count": leave_count,
                "role_summaries": role_summaries,
                "people": [
                    {
                        "name": shift.user.get_full_name() or shift.user.username,
                        "role": user_role_label(shift.user),
                        "duty": shift.note,
                    }
                    for shift in scheduled_shifts
                ],
            })

        timetable_rows.append({
            **day_info,
            "cells": cells,
        })

    # Doctor-roster style matrix: rows are weekdays, columns are staff roles.
    # Each cell shows the real staff names grouped by night/morning/evening.
    role_columns = [
        {
            "role": role,
            "label": label,
            "is_selected": role == role_filter,
        }
        for role, label in schedule_role_choices
    ]
    # The main roster must always show every hospital position. Filters only
    # narrow the detail section below the timetable, never remove columns.
    matrix_source = all_week_schedules + admin_week_schedules
    role_day_shifts = defaultdict(lambda: {"night": [], "morning": [], "evening": [], "leave": []})
    for shift in matrix_source:
        if shift.status == ShiftSchedule.Status.CANCELLED:
            continue
        role = user_role_key(shift.user)
        if shift.status == ShiftSchedule.Status.LEAVE:
            role_day_shifts[(shift.shift_date, role)]["leave"].append(shift)
            continue
        if shift.start_time.hour == 0:
            bucket = "night"
        elif shift.start_time.hour == 8:
            bucket = "morning"
        elif shift.start_time.hour == 16:
            bucket = "evening"
        else:
            continue
        role_day_shifts[(shift.shift_date, role)][bucket].append(shift)

    role_timetable_rows = []
    for day_info in week_days:
        cells = []
        for role_column in role_columns:
            grouped = role_day_shifts[(day_info["date"], role_column["role"])]
            cells.append({
                "role": role_column["role"],
                "label": role_column["label"],
                "night": grouped["night"],
                "morning": grouped["morning"],
                "evening": grouped["evening"],
                "leave": grouped["leave"],
                "has_any": any(grouped[key] for key in ("night", "morning", "evening", "leave")),
            })
        role_timetable_rows.append({
            **day_info,
            "cells": cells,
        })

    # New roster presentation: a compact selected-day overview plus a
    # role-focused 7-day x 3-shift table. Keep the old matrix context above
    # for backwards compatibility/tests, but the UI no longer renders all
    # ten roles side by side.
    selected_roster_role = role_filter or StaffProfile.Role.DOCTOR
    role_label_map = dict(schedule_role_choices)
    role_icon_map = {
        ADMIN_ROLE: "icon-settings",
        StaffProfile.Role.DOCTOR: "icon-stethoscope",
        StaffProfile.Role.NURSE: "icon-activity",
        StaffProfile.Role.NURSE_ASSISTANT: "icon-users",
        StaffProfile.Role.EMERGENCY: "icon-alert",
        StaffProfile.Role.STAFF: "icon-user-plus",
        StaffProfile.Role.QUEUE_OPERATOR: "icon-ticket",
        StaffProfile.Role.BIOMEDICAL: "icon-watch",
        StaffProfile.Role.PHARMACIST: "icon-hospital",
        StaffProfile.Role.CASHIER: "icon-chart",
    }
    selected_roster_role_label = role_label_map.get(
        selected_roster_role,
        selected_roster_role,
    )

    weekly_role_stats = defaultdict(lambda: {"shift_count": 0, "staff_ids": set()})
    for shift in matrix_source:
        if shift.status == ShiftSchedule.Status.CANCELLED:
            continue
        role = user_role_key(shift.user)
        weekly_role_stats[role]["shift_count"] += 1
        weekly_role_stats[role]["staff_ids"].add(shift.user_id)

    active_people_by_role = defaultdict(int)
    for person in users:
        active_people_by_role[user_role_key(person)] += 1
    active_people_by_role[ADMIN_ROLE] = len(admin_users)

    role_cards = []
    for role, label in schedule_role_choices:
        stats = weekly_role_stats[role]
        role_cards.append({
            "role": role,
            "label": label,
            "icon": role_icon_map.get(role, "icon-users"),
            "people_count": active_people_by_role.get(role, 0),
            "scheduled_people_count": len(stats["staff_ids"]),
            "shift_count": stats["shift_count"],
            "is_selected": role == selected_roster_role,
        })

    def roster_visible(shifts):
        visible = []
        for shift in shifts:
            if search_query and not matches_search(shift.user):
                continue
            visible.append(shift)
        return visible

    role_week_rows = []
    for day_info in week_days:
        grouped = role_day_shifts[(day_info["date"], selected_roster_role)]
        role_week_rows.append({
            **day_info,
            "night": roster_visible(grouped["night"]),
            "morning": roster_visible(grouped["morning"]),
            "evening": roster_visible(grouped["evening"]),
            "leave": roster_visible(grouped["leave"]),
        })

    selected_day_scheduled = [
        shift
        for shift in matrix_source
        if shift.shift_date == selected
        and shift.status == ShiftSchedule.Status.SCHEDULED
        and (not search_query or matches_search(shift.user))
    ]
    selected_day_shift_cards = []
    selected_day_shift_totals = {"night": 0, "morning": 0, "evening": 0}
    for shift_key, shift_label, time_label, shift_hour in timetable_shift_defs:
        shift_people = [
            shift for shift in selected_day_scheduled
            if shift.start_time.hour == shift_hour
        ]
        selected_day_shift_totals[shift_key] = len(shift_people)

        by_role = defaultdict(list)
        for shift in shift_people:
            by_role[user_role_key(shift.user)].append(shift)

        role_groups = []
        for role, label in schedule_role_choices:
            role_shifts = by_role.get(role, [])
            if not role_shifts:
                continue
            role_groups.append({
                "role": role,
                "label": label,
                "shifts": role_shifts,
                "count": len(role_shifts),
            })

        selected_day_shift_cards.append({
            "key": shift_key,
            "label": shift_label,
            "time_label": time_label,
            "total": len(shift_people),
            "role_groups": role_groups,
        })

    selected_day_staff_total = len({
        shift.user_id for shift in selected_day_scheduled
    })
    selected_day_role_total = len({
        user_role_key(shift.user) for shift in selected_day_scheduled
    })

    role_count_map = defaultdict(int)
    for shift in schedules:
        role_count_map[user_role_key(shift.user)] += 1
    role_counts = [
        {"role": role, "label": label, "total": role_count_map.get(role, 0)}
        for role, label in schedule_role_choices
        if role_count_map.get(role, 0)
    ]

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
        StaffDuty.objects.filter(duty_date=today, is_present=True, user__in=schedulable_users)
        .select_related("user", "user__hospital_staff_profile")
    ):
        planned = today_planned.get(duty.user_id)
        on_duty_now.append({
            "user": duty.user,
            "profile": getattr(duty.user, "hospital_staff_profile", None),
            "role_label": user_role_label(duty.user),
            "case_count": active_case_counts.get(duty.user_id, 0),
            "planned_duty": planned.note if planned and planned.note else "ยังไม่ได้กำหนดหน้าที่",
            "is_current_user": duty.user_id == request.user.id,
        })

    return render(request, "queues/shift_schedule_roles.html", {
        "can_manage": can_manage,
        "users": schedulable_users,
        "admin_users": admin_users,
        "selected_day": selected,
        "selected_day_label": THAI_WEEKDAYS[selected.weekday()],
        "selected_shifts": schedules,
        "week_days": week_days,
        "week_dates": week_dates,
        "board_rows": board_rows,
        "role_sections": role_sections,
        "role_counts": role_counts,
        "role_choices": schedule_role_choices,
        "role_filter": role_filter,
        "search_query": search_query,
        "selected_shift_summary": selected_shift_summary,
        "overview_role_rows": overview_role_rows,
        "detailed_mode": detailed_mode,
        "timetable_shift_defs": timetable_shift_defs,
        "timetable_rows": timetable_rows,
        "role_columns": role_columns,
        "role_timetable_rows": role_timetable_rows,
        "selected_roster_role": selected_roster_role,
        "selected_roster_role_label": selected_roster_role_label,
        "role_cards": role_cards,
        "role_week_rows": role_week_rows,
        "selected_day_shift_cards": selected_day_shift_cards,
        "selected_day_shift_totals": selected_day_shift_totals,
        "selected_day_staff_total": selected_day_staff_total,
        "selected_day_role_total": selected_day_role_total,
        "on_duty_now": on_duty_now,
        "week_start": week_start,
        "week_end": week_end,
        "previous_day": previous_day,
        "next_day": next_day,
        "previous_week": previous_week,
        "next_week": next_week,
        "shift_statuses": ShiftSchedule.Status.choices,
        "duty_suggestions": DEFAULT_DUTY_SUGGESTIONS,
    })
