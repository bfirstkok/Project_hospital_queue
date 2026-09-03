import mimetypes
from pathlib import Path

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.http import FileResponse, Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_http_methods

from .care_workload import (
    MAX_PATIENTS_PER_NURSE,
    assign_visit_to_nurse,
    auto_assign_visit,
    end_assignment_for_visit,
    handover_nurse_cases,
    nurse_workload_rows,
)
from .models import DeviceAssignment, NurseCareAssignment, Queue, StaffDuty, StaffProfile, Visit


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
@require_GET
def staff_heartbeat(request):
    return JsonResponse({"online": True, "at": timezone.now().isoformat()})


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
def personnel_dashboard(request):
    now = timezone.now()
    today = timezone.localdate(now)
    user_model = get_user_model()

    if request.method == "POST":
        action = request.POST.get("action", "")

        if action == "set_attendance":
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
            end_assignment_for_visit(care_assignment.visit)
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
        .filter(is_active=True)
        .select_related("hospital_staff_profile")
        .order_by("first_name", "username")
    )
    existing_profile_ids = {user.id for user in users if hasattr(user, "hospital_staff_profile")}
    StaffProfile.objects.bulk_create(
        [StaffProfile(user=user) for user in users if user.id not in existing_profile_ids],
        ignore_conflicts=True,
    )
    if len(existing_profile_ids) != len(users):
        users = list(
            user_model.objects
            .filter(is_active=True)
            .select_related("hospital_staff_profile")
            .order_by("first_name", "username")
        )

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
            "duty": duty,
            "profile": profile,
            "is_present": bool(duty and duty.is_present),
            "is_available": bool(nurse_data and nurse_data["is_available"]),
            "ready": bool(nurse_data and nurse_data["ready"]),
            "patient_count": nurse_data["patient_count"] if nurse_data else 0,
            "remaining_capacity": nurse_data["remaining_capacity"] if nurse_data else MAX_PATIENTS_PER_NURSE,
            "load_state": nurse_data["load_state"] if nurse_data else "none",
            "active_cases": nurse_data["active_cases"] if nurse_data else [],
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
        "role_counts": {
            role: sum(row["profile"].role == role for row in staff_rows)
            for role, _label in StaffProfile.Role.choices
        },
    })
