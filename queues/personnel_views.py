import mimetypes
from datetime import date, timedelta
from pathlib import Path

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.db import IntegrityError
from django.db.models import Count, Q
from django.http import FileResponse, Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_http_methods
from accounts.access import Capability, has_capability
from accounts.models import AccountStatusLog

from .care_workload import (
    MAX_PATIENTS_PER_NURSE,
    assign_visit_to_nurse,
    auto_assign_visit,
    end_assignment_for_visit,
    handover_nurse_cases,
    nurse_workload_rows,
)
from .models import DeviceAssignment, NurseCareAssignment, Queue, ShiftSchedule, StaffDuty, StaffProfile, Visit


CARE_QUEUE_STATUSES = {
    Queue.Status.OBSERVATION_MONITORING,
    Queue.Status.REASSESSMENT_REQUIRED,
    Queue.Status.MONITORING,
}


def _eligible_device_assignments():
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


def _is_available_nurse(staff_user, duty):
    profile = getattr(staff_user, "hospital_staff_profile", None)
    return bool(
        profile
        and profile.role == StaffProfile.Role.NURSE
        and duty
        and duty.is_present
        and duty.is_available
    )


def _handover_message(name, result):
    text = f"ส่งต่อเคสของ {name}: ย้ายสำเร็จ {result.moved} เคส"
    if result.unassigned:
        text += f" · ยังไม่มีผู้รับช่วง {result.unassigned} เคส"
    if result.skipped:
        text += f" · ข้าม {result.skipped} เคส"
    return text


def _care_visits():
    """Patients that should be visible to the head-nurse workload dashboard."""
    return (
        Visit.objects
        .select_related("patient", "queue")
        .filter(
            Q(
                final_severity=Visit.Severity.YELLOW,
                queue__status__in=[
                    Queue.Status.WAITING_QUEUE,
                    Queue.Status.CALLED,
                    Queue.Status.OBSERVATION_MONITORING,
                    Queue.Status.REASSESSMENT_REQUIRED,
                ],
            )
            | Q(queue__status=Queue.Status.MONITORING)
            | Q(nurse_care_assignments__is_active=True)
        )
        .distinct()
        .order_by("queue__priority", "queue__created_at")
    )


@login_required
@require_http_methods(["GET", "POST"])
def staff_heartbeat(request):
    now = timezone.now()
    today = timezone.localdate(now)
    duty = StaffDuty.objects.filter(user=request.user, duty_date=today).first()

    if request.method == "POST":
        action = request.POST.get("action")
        profile = getattr(request.user, "hospital_staff_profile", None)
        is_nurse = bool(profile and profile.role == StaffProfile.Role.NURSE)

        if action == "check_in":
            duty, _ = StaffDuty.objects.get_or_create(
                user=request.user,
                duty_date=today,
                defaults={"checked_in_at": now},
            )
            duty.is_present = True
            duty.is_available = is_nurse
            duty.checked_in_at = now
            duty.checked_out_at = None
            duty.last_seen_at = now
            duty.save(update_fields=[
                "is_present", "is_available", "checked_in_at", "checked_out_at", "last_seen_at",
            ])
        elif action == "check_out":
            active_cases = NurseCareAssignment.objects.filter(
                nurse=request.user,
                is_active=True,
            ).count()
            if active_cases:
                return JsonResponse({
                    "ok": False,
                    "message": f"ยังมีผู้ป่วยในความดูแล {active_cases} ราย กรุณาส่งต่อเวรก่อน",
                }, status=409)
            if duty:
                duty.is_present = False
                duty.is_available = False
                duty.checked_out_at = now
                duty.last_seen_at = now
                duty.save(update_fields=["is_present", "is_available", "checked_out_at", "last_seen_at"])
        else:
            return JsonResponse({"ok": False, "message": "คำสั่งไม่ถูกต้อง"}, status=400)

    duty = StaffDuty.objects.filter(user=request.user, duty_date=today).first()
    profile = getattr(request.user, "hospital_staff_profile", None)
    return JsonResponse({
        "ok": True,
        "online": True,
        "at": now.isoformat(),
        "is_present": bool(duty and duty.is_present),
        "is_available": bool(duty and duty.is_available),
        "is_nurse": bool(profile and profile.role == StaffProfile.Role.NURSE),
    })


@login_required
@require_GET
def staff_photo(request, profile_id):
    profile = get_object_or_404(StaffProfile, pk=profile_id)
    if not profile.photo:
        raise Http404("Staff photo not found")
    content_type = mimetypes.guess_type(profile.photo.name)[0] or "application/octet-stream"
    response = FileResponse(profile.photo.open("rb"), content_type=content_type)
    response["Cache-Control"] = "private, max-age=3600"
    response["X-Content-Type-Options"] = "nosniff"
    return response


@login_required
@require_http_methods(["GET", "POST"])
def shift_schedule(request):
    can_manage = has_capability(request.user, Capability.MANAGE_PERSONNEL)
    user_model = get_user_model()

    if request.method == "POST":
        if not can_manage:
            return render(
                request,
                "403.html",
                {
                    "required_capability": Capability.MANAGE_PERSONNEL,
                    "current_role": getattr(
                        getattr(request.user, "hospital_staff_profile", None),
                        "role",
                        "ADMIN" if request.user.is_superuser else "STAFF",
                    ),
                },
                status=403,
            )
        action = request.POST.get("action")
        return_day = request.POST.get("day") or request.POST.get("week", "")
        if action == "delete_shift":
            shift = get_object_or_404(ShiftSchedule, pk=request.POST.get("shift_id"))
            shift.delete()
            messages.success(request, "ลบเวรที่วางไว้แล้ว")
        elif action == "save_shift":
            try:
                shift_date = date.fromisoformat(request.POST.get("shift_date", ""))
                start_time = timezone.datetime.strptime(request.POST.get("start_time", ""), "%H:%M").time()
                end_time = timezone.datetime.strptime(request.POST.get("end_time", ""), "%H:%M").time()
            except (TypeError, ValueError):
                messages.error(request, "วันที่หรือเวลาเวรไม่ถูกต้อง")
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
            shift_id = request.POST.get("shift_id")
            shift = get_object_or_404(ShiftSchedule, pk=shift_id) if shift_id else ShiftSchedule()
            shift.user = staff_user
            shift.shift_date = shift_date
            shift.start_time = start_time
            shift.end_time = end_time
            shift.status = status
            shift.note = request.POST.get("note", "").strip()[:200]
            shift.created_by = shift.created_by or request.user
            try:
                shift.save()
            except IntegrityError:
                messages.error(request, "บุคลากรคนนี้มีเวรที่เริ่มเวลาเดียวกันอยู่แล้ว")
            else:
                messages.success(request, "บันทึกตารางเวรแล้ว")
        else:
            messages.error(request, "คำสั่งไม่ถูกต้อง")
        return redirect(f"{request.path}?day={return_day}")

    day_value = request.GET.get("day") or request.GET.get("week", "")
    try:
        selected = date.fromisoformat(day_value) if day_value else timezone.localdate()
    except ValueError:
        selected = timezone.localdate()
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
        .order_by("start_time", "user__hospital_staff_profile__role", "user__first_name")
    )
    duties = {
        (duty.user_id, duty.duty_date): duty
        for duty in StaffDuty.objects.filter(duty_date=selected, user__in=users)
    }
    active_case_counts = dict(
        NurseCareAssignment.objects.filter(is_active=True)
        .values("nurse_id").annotate(total=Count("id"))
        .values_list("nurse_id", "total")
    )
    for shift in schedules:
        duty = duties.get((shift.user_id, shift.shift_date))
        shift.actual_duty = duty
        shift.active_case_count = active_case_counts.get(shift.user_id, 0)
    thai_weekdays = ("จันทร์", "อังคาร", "พุธ", "พฤหัสบดี", "ศุกร์", "เสาร์", "อาทิตย์")
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
            "label": thai_weekdays[offset],
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
    on_duty_now = [
        {
            "user": duty.user,
            "profile": duty.user.hospital_staff_profile,
            "case_count": active_case_counts.get(duty.user_id, 0),
        }
        for duty in StaffDuty.objects.filter(
            duty_date=timezone.localdate(), is_present=True, user__in=users,
        ).select_related("user", "user__hospital_staff_profile")
    ]
    return render(request, "queues/shift_schedule.html", {
        "can_manage": can_manage,
        "users": users,
        "selected_day": selected,
        "selected_day_label": thai_weekdays[selected.weekday()],
        "selected_shifts": schedules,
        "week_days": week_days,
        "role_counts": role_counts,
        "on_duty_now": on_duty_now,
        "week_start": week_start,
        "week_end": week_end,
        "previous_day": previous_day,
        "next_day": next_day,
        "shift_statuses": ShiftSchedule.Status.choices,
    })


@login_required
@require_http_methods(["GET", "POST"])
def personnel_dashboard(request):
    now = timezone.now()
    today = timezone.localdate(now)
    user_model = get_user_model()
    can_manage = has_capability(request.user, Capability.MANAGE_PERSONNEL)

    if request.method == "POST":
        if not has_capability(request.user, Capability.MANAGE_PERSONNEL):
            return render(
                request,
                "403.html",
                {
                    "required_capability": Capability.MANAGE_PERSONNEL,
                    "current_role": getattr(
                        getattr(request.user, "hospital_staff_profile", None),
                        "role",
                        "ADMIN" if request.user.is_superuser else "STAFF",
                    ),
                },
                status=403,
            )
        action = request.POST.get("action", "")

        if action == "set_account_status":
            staff_user = get_object_or_404(
                user_model.objects.select_related("hospital_staff_profile"),
                pk=request.POST.get("user_id"),
            )
            if staff_user.is_superuser:
                messages.error(request, "ไม่อนุญาตให้ระงับหรือเปิดใช้งานบัญชีผู้ดูแลระบบสูงสุดจากหน้านี้")
                return redirect("personnel_dashboard")
            if staff_user.pk == request.user.pk:
                messages.error(request, "ไม่สามารถเปลี่ยนสถานะบัญชีของตนเองจากหน้านี้ได้")
                return redirect("personnel_dashboard")

            reason = request.POST.get("reason", "").strip()
            if len(reason) < 3:
                messages.error(request, "กรุณาระบุเหตุผลอย่างน้อย 3 ตัวอักษรเพื่อเก็บ Audit Trail")
                return redirect("personnel_dashboard")

            new_is_active = request.POST.get("is_active") == "1"
            if staff_user.is_active == new_is_active:
                messages.warning(request, "บัญชีอยู่ในสถานะที่เลือกอยู่แล้ว")
                return redirect("personnel_dashboard")

            if not new_is_active:
                StaffDuty.objects.filter(user=staff_user, duty_date=today).update(
                    is_present=False,
                    is_available=False,
                    checked_out_at=now,
                    last_seen_at=now,
                )
                profile = getattr(staff_user, "hospital_staff_profile", None)
                if profile and profile.role == StaffProfile.Role.NURSE:
                    result = handover_nurse_cases(from_nurse=staff_user, assigned_by=request.user)
                    if result.moved or result.unassigned:
                        messages.warning(
                            request,
                            _handover_message(staff_user.get_full_name() or staff_user.username, result),
                        )

            staff_user.is_active = new_is_active
            staff_user.save(update_fields=["is_active"])
            AccountStatusLog.objects.create(
                user=staff_user,
                action=(
                    AccountStatusLog.Action.ACTIVATE
                    if new_is_active
                    else AccountStatusLog.Action.SUSPEND
                ),
                reason=reason,
                actor=request.user,
            )
            messages.success(
                request,
                (
                    f"เปิดใช้งานบัญชี {staff_user.get_full_name() or staff_user.username} แล้ว"
                    if new_is_active
                    else f"ระงับบัญชี {staff_user.get_full_name() or staff_user.username} แล้ว"
                ),
            )

        elif action == "set_attendance":
            staff_user = get_object_or_404(user_model, pk=request.POST.get("user_id"), is_active=True)
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
                duty.is_available = False
            duty.save(update_fields=["is_present", "is_available", "checked_in_at", "checked_out_at"])

            profile = getattr(staff_user, "hospital_staff_profile", None)
            if not is_present and profile and profile.role == StaffProfile.Role.NURSE:
                result = handover_nurse_cases(from_nurse=staff_user, assigned_by=request.user)
                if result.moved or result.unassigned:
                    messages.warning(request, _handover_message(staff_user.get_full_name() or staff_user.username, result))
            messages.success(request, f"อัปเดตสถานะ {staff_user.get_full_name() or staff_user.username} แล้ว")

        elif action == "set_availability":
            staff_user = get_object_or_404(
                user_model.objects.select_related("hospital_staff_profile"),
                pk=request.POST.get("user_id"),
                is_active=True,
                hospital_staff_profile__role=StaffProfile.Role.NURSE,
            )
            duty = get_object_or_404(StaffDuty, user=staff_user, duty_date=today, is_present=True)
            is_available = request.POST.get("is_available") == "1"
            duty.is_available = is_available
            duty.save(update_fields=["is_available"])
            if not is_available:
                result = handover_nurse_cases(from_nurse=staff_user, assigned_by=request.user)
                if result.moved or result.unassigned:
                    messages.warning(request, _handover_message(staff_user.get_full_name() or staff_user.username, result))
            messages.success(request, f"อัปเดตความพร้อมของ {staff_user.get_full_name() or staff_user.username} แล้ว")

        elif action == "set_staff_role":
            staff_user = get_object_or_404(user_model, pk=request.POST.get("user_id"), is_active=True)
            role = request.POST.get("role", "")
            valid_roles = {value for value, _label in StaffProfile.Role.choices}
            if role not in valid_roles:
                messages.error(request, "ประเภทบุคลากรไม่ถูกต้อง")
                return redirect("personnel_dashboard")
            profile, _ = StaffProfile.objects.get_or_create(user=staff_user)
            was_nurse = profile.role == StaffProfile.Role.NURSE
            if was_nurse and role != StaffProfile.Role.NURSE:
                StaffDuty.objects.filter(user=staff_user, duty_date=today).update(is_available=False)
                result = handover_nurse_cases(from_nurse=staff_user, assigned_by=request.user)
                if result.moved or result.unassigned:
                    messages.warning(request, _handover_message(staff_user.get_full_name() or staff_user.username, result))
            profile.role = role
            profile.save(update_fields=["role"])
            messages.success(request, f"กำหนดประเภทของ {staff_user.get_full_name() or staff_user.username} แล้ว")

        elif action == "update_staff_identity":
            staff_user = get_object_or_404(user_model, pk=request.POST.get("user_id"), is_active=True)
            first_name = request.POST.get("first_name", "").strip()
            last_name = request.POST.get("last_name", "").strip()
            if not first_name or not last_name:
                messages.error(request, "กรุณากรอกชื่อจริงและนามสกุลให้ครบ")
                return redirect("personnel_dashboard")
            if len(first_name) > 150 or len(last_name) > 150:
                messages.error(request, "ชื่อและนามสกุลต้องยาวไม่เกิน 150 ตัวอักษร")
                return redirect("personnel_dashboard")

            profile, _ = StaffProfile.objects.get_or_create(user=staff_user)
            photo = request.FILES.get("photo")
            if photo:
                allowed_types = {"image/jpeg", "image/png", "image/webp"}
                allowed_extensions = {".jpg", ".jpeg", ".png", ".webp"}
                if photo.content_type not in allowed_types or Path(photo.name).suffix.lower() not in allowed_extensions:
                    messages.error(request, "รองรับรูป JPG, PNG หรือ WebP เท่านั้น")
                    return redirect("personnel_dashboard")
                if photo.size > 5 * 1024 * 1024:
                    messages.error(request, "รูปต้องมีขนาดไม่เกิน 5 MB")
                    return redirect("personnel_dashboard")
                old_photo_name = profile.photo.name if profile.photo else ""
                profile.photo = photo
                profile.save(update_fields=["photo"])
                if old_photo_name and old_photo_name != profile.photo.name:
                    profile.photo.storage.delete(old_photo_name)

            staff_user.first_name = first_name
            staff_user.last_name = last_name
            staff_user.save(update_fields=["first_name", "last_name"])
            messages.success(request, f"บันทึกข้อมูล {staff_user.get_full_name()} แล้ว")

        elif action in {"assign_patient", "reassign_patient"}:
            nurse = get_object_or_404(
                user_model.objects.select_related("hospital_staff_profile"),
                pk=request.POST.get("nurse_id"),
                is_active=True,
                hospital_staff_profile__role=StaffProfile.Role.NURSE,
            )
            visit = get_object_or_404(Visit.objects.select_related("queue", "patient"), pk=request.POST.get("visit_id"))
            try:
                assignment, count = assign_visit_to_nurse(
                    visit=visit,
                    nurse=nurse,
                    assigned_by=request.user,
                )
            except ValueError as exc:
                code = str(exc)
                if code == "nurse_full":
                    messages.error(request, f"พยาบาลคนนี้ดูแลครบ {MAX_PATIENTS_PER_NURSE} คนแล้ว")
                elif code == "nurse_unavailable":
                    messages.error(request, "เลือกได้เฉพาะพยาบาลที่ขึ้นเวรและพร้อมรับผู้ป่วย")
                else:
                    messages.error(request, "ผู้ป่วยต้องอยู่ในกลุ่มเฝ้าระวัง/ติดตามก่อนจึงมอบหมายจากหน้านี้ได้")
            else:
                messages.success(
                    request,
                    f"มอบหมาย {visit.patient} ให้ {assignment.nurse.get_full_name() or assignment.nurse.username} "
                    f"ดูแลแล้ว ({count}/{MAX_PATIENTS_PER_NURSE})",
                )

        elif action == "auto_assign_patient":
            visit = get_object_or_404(Visit.objects.select_related("queue", "patient"), pk=request.POST.get("visit_id"))
            try:
                assignment, count = auto_assign_visit(visit=visit, assigned_by=request.user)
            except ValueError:
                messages.error(request, "ยังไม่มีพยาบาลที่ว่างและพร้อมรับเคสนี้")
            else:
                messages.success(
                    request,
                    f"ระบบเลือก {assignment.nurse.get_full_name() or assignment.nurse.username} ให้อัตโนมัติ "
                    f"({count}/{MAX_PATIENTS_PER_NURSE})",
                )

        elif action == "end_assignment":
            care_assignment = get_object_or_404(
                NurseCareAssignment.objects.select_related("visit"),
                pk=request.POST.get("assignment_id"),
                is_active=True,
            )
            end_assignment_for_visit(
                care_assignment.visit,
                actor=request.user,
                reason="ผู้ดูแลเวรยุติการมอบหมายผู้ป่วย",
            )
            messages.success(request, "ยุติการมอบหมายผู้ป่วยแล้ว")

        elif action == "handover_nurse":
            from_nurse = get_object_or_404(
                user_model.objects.select_related("hospital_staff_profile"),
                pk=request.POST.get("from_nurse_id"),
                is_active=True,
                hospital_staff_profile__role=StaffProfile.Role.NURSE,
            )
            target_id = request.POST.get("to_nurse_id", "").strip()
            target_nurse = None
            if target_id:
                target_nurse = get_object_or_404(
                    user_model.objects.select_related("hospital_staff_profile"),
                    pk=target_id,
                    is_active=True,
                    hospital_staff_profile__role=StaffProfile.Role.NURSE,
                )
                if target_nurse.id == from_nurse.id:
                    messages.error(request, "พยาบาลผู้รับช่วงต้องเป็นคนละคนกับผู้ส่งต่อ")
                    return redirect("personnel_dashboard")
            result = handover_nurse_cases(
                from_nurse=from_nurse,
                target_nurse=target_nurse,
                assigned_by=request.user,
            )
            if result.unassigned:
                messages.warning(request, _handover_message(from_nurse.get_full_name() or from_nurse.username, result))
            else:
                messages.success(request, _handover_message(from_nurse.get_full_name() or from_nurse.username, result))

        elif action == "auto_assign_all_unassigned":
            moved = 0
            failed = 0
            active_visit_ids = set(NurseCareAssignment.objects.filter(is_active=True).values_list("visit_id", flat=True))
            for visit in _care_visits():
                if visit.id in active_visit_ids:
                    continue
                try:
                    auto_assign_visit(visit=visit, assigned_by=request.user)
                    moved += 1
                except ValueError:
                    failed += 1
            if moved:
                messages.success(request, f"มอบหมายอัตโนมัติสำเร็จ {moved} เคส")
            if failed:
                messages.warning(request, f"ยังมอบหมายไม่ได้ {failed} เคส เนื่องจากไม่มีพยาบาลว่างหรือสถานะยังไม่พร้อม")

        else:
            messages.error(request, "คำสั่งไม่ถูกต้อง")

        return redirect("personnel_dashboard")

    users = list(
        user_model.objects
        .filter(hospital_staff_profile__isnull=False)
        .select_related("hospital_staff_profile")
        .order_by("-is_active", "first_name", "username")
    )

    latest_account_status = {}
    for status_log in (
        AccountStatusLog.objects
        .filter(user__in=users)
        .select_related("actor")
        .order_by("-created_at", "-id")
    ):
        latest_account_status.setdefault(status_log.user_id, status_log)

    duties = {
        duty.user_id: duty
        for duty in StaffDuty.objects.filter(duty_date=today, user__in=users)
    }
    nurse_rows = nurse_workload_rows(today=today)
    nurse_row_by_id = {row["user"].id: row for row in nurse_rows}

    staff_rows = []
    for staff_user in users:
        duty = duties.get(staff_user.id)
        profile = staff_user.hospital_staff_profile
        nurse_data = nurse_row_by_id.get(staff_user.id)
        row = {
            "user": staff_user,
            "name": staff_user.get_full_name() or staff_user.username,
            "is_system_admin": staff_user.is_superuser,
            "is_account_active": staff_user.is_active,
            "latest_account_status": latest_account_status.get(staff_user.id),
            "role_label": (
                "ผู้ดูแลระบบสูงสุด"
                if staff_user.is_superuser
                else profile.get_role_display()
            ),
            "duty": duty,
            "profile": profile,
            "is_present": bool(duty and duty.is_present),
            "is_available": bool(nurse_data and nurse_data["is_available"]),
            "ready": bool(nurse_data and nurse_data["ready"]),
            "patient_count": nurse_data["patient_count"] if nurse_data else 0,
            "remaining_capacity": nurse_data["remaining_capacity"] if nurse_data else MAX_PATIENTS_PER_NURSE,
            "load_state": nurse_data["load_state"] if nurse_data else "none",
            "active_cases": (
                nurse_data["active_cases"]
                if nurse_data and (can_manage or staff_user.id == request.user.id)
                else []
            ),
        }
        staff_rows.append(row)

    staff_rows.sort(key=lambda row: (
        not row["is_present"],
        row["profile"].role == StaffProfile.Role.NURSE and row["load_state"] == "full",
        row["patient_count"] if row["profile"].role == StaffProfile.Role.NURSE else 0,
        row["name"].casefold(),
    ))

    active_assignments = list(
        NurseCareAssignment.objects
        .select_related("nurse", "nurse__hospital_staff_profile", "visit", "visit__patient", "visit__queue")
        .filter(is_active=True)
    )
    active_care = {item.visit_id: item for item in active_assignments}
    device_by_visit = {
        item.visit_id: item.device
        for item in _eligible_device_assignments()
    }

    patient_rows = []
    for visit in _care_visits():
        q = visit.queue
        care_assignment = active_care.get(visit.id)
        patient_rows.append({
            "visit": visit,
            "patient": visit.patient,
            "queue": q,
            "device": device_by_visit.get(visit.id),
            "care_assignment": care_assignment,
            "care_type": (
                "ติดตามหลังตรวจ"
                if q.status == Queue.Status.MONITORING
                else "ต้องประเมินซ้ำ"
                if q.status == Queue.Status.REASSESSMENT_REQUIRED
                else "เฝ้าระวังสีเหลือง"
            ),
            "needs_reassessment": q.status == Queue.Status.REASSESSMENT_REQUIRED,
        })

    # The personnel page doubles as a nurse workspace. A regular nurse sees
    # only patients currently assigned to that account; coordinators/admins
    # retain the complete assignment board.
    if not can_manage:
        patient_rows = [
            row
            for row in patient_rows
            if row["care_assignment"] and row["care_assignment"].nurse_id == request.user.id
        ]

    assignable_nurses = [row for row in nurse_rows if row["ready"]]
    ready_nurse_count = len(assignable_nurses)
    full_nurse_count = sum(row["is_present"] and row["patient_count"] >= MAX_PATIENTS_PER_NURSE for row in nurse_rows)
    on_duty_nurse_count = sum(row["is_present"] for row in nurse_rows)
    unassigned_count = sum(not row["care_assignment"] for row in patient_rows)
    reassessment_count = sum(row["needs_reassessment"] for row in patient_rows)

    active_filter = request.GET.get("filter", "all")

    return render(request, "queues/personnel_dashboard_v2.html", {
        "today": today,
        "staff_rows": staff_rows,
        "nurse_rows": nurse_rows,
        "available_nurses": assignable_nurses,
        "assignable_nurses": assignable_nurses,
        "staff_role_choices": StaffProfile.Role.choices,
        "patient_rows": patient_rows,
        "max_patients_per_nurse": MAX_PATIENTS_PER_NURSE,
        "present_count": sum(row["is_present"] for row in staff_rows),
        "available_count": ready_nurse_count,
        "ready_nurse_count": ready_nurse_count,
        "full_nurse_count": full_nurse_count,
        "on_duty_nurse_count": on_duty_nurse_count,
        "unassigned_count": unassigned_count,
        "reassessment_count": reassessment_count,
        "assigned_count": sum(bool(row["care_assignment"]) for row in patient_rows),
        "active_filter": active_filter,
        "focus_visit_id": request.GET.get("visit", ""),
        "can_manage_personnel": can_manage,
        "personal_assignments_only": not can_manage,
        "role_counts": {
            role: sum(
                not row["is_system_admin"] and row["profile"].role == role
                for row in staff_rows
            )
            for role, _label in StaffProfile.Role.choices
        },
        "system_admin_count": sum(row["is_system_admin"] for row in staff_rows),
    })
