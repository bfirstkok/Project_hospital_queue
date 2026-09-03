import mimetypes
from pathlib import Path

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import FileResponse, Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_http_methods

from .models import DeviceAssignment, NurseCareAssignment, Queue, StaffDuty, StaffProfile, Visit


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


def _is_available_nurse(staff_user, duty):
    profile = getattr(staff_user, "hospital_staff_profile", None)
    return bool(
        profile
        and profile.role == StaffProfile.Role.NURSE
        and duty
        and duty.is_present
        and duty.is_available
    )


@login_required
@require_GET
def staff_heartbeat(request):
    """Lightweight request used by open staff pages to keep online status fresh."""
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
                duty.is_available = False
            duty.save(update_fields=["is_present", "is_available", "checked_in_at", "checked_out_at"])
            messages.success(
                request,
                f"อัปเดตสถานะ {staff_user.get_full_name() or staff_user.username} แล้ว",
            )

        elif action == "set_availability":
            staff_user = get_object_or_404(
                get_user_model().objects.select_related("hospital_staff_profile"),
                pk=request.POST.get("user_id"),
                is_active=True,
                hospital_staff_profile__role=StaffProfile.Role.NURSE,
            )
            duty = get_object_or_404(
                StaffDuty,
                user=staff_user,
                duty_date=today,
                is_present=True,
            )
            duty.is_available = request.POST.get("is_available") == "1"
            duty.save(update_fields=["is_available"])
            messages.success(
                request,
                f"อัปเดตความพร้อมของ {staff_user.get_full_name() or staff_user.username} แล้ว",
            )

        elif action == "set_staff_role":
            staff_user = get_object_or_404(
                get_user_model(),
                pk=request.POST.get("user_id"),
                is_active=True,
            )
            role = request.POST.get("role", "")
            valid_roles = {value for value, _label in StaffProfile.Role.choices}
            if role not in valid_roles:
                messages.error(request, "ประเภทบุคลากรไม่ถูกต้อง")
                return redirect("personnel_dashboard")
            profile, _ = StaffProfile.objects.get_or_create(user=staff_user)
            profile.role = role
            profile.save(update_fields=["role"])
            if role != StaffProfile.Role.NURSE:
                StaffDuty.objects.filter(
                    user=staff_user,
                    duty_date=today,
                ).update(is_available=False)
            messages.success(
                request,
                f"กำหนดประเภทของ {staff_user.get_full_name() or staff_user.username} แล้ว",
            )

        elif action == "update_staff_identity":
            staff_user = get_object_or_404(
                get_user_model(),
                pk=request.POST.get("user_id"),
                is_active=True,
            )
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

        elif action == "assign_patient":
            nurse = get_object_or_404(
                get_user_model().objects.select_related("hospital_staff_profile"),
                pk=request.POST.get("nurse_id"),
                is_active=True,
                hospital_staff_profile__role=StaffProfile.Role.NURSE,
            )
            duty = StaffDuty.objects.filter(user=nurse, duty_date=today).first()
            if not _is_available_nurse(nurse, duty):
                messages.error(request, "เลือกมอบหมายได้เฉพาะพยาบาลที่ขึ้นเวรและพร้อมรับผู้ป่วย")
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

    users = list(
        get_user_model().objects
        .filter(is_active=True)
        .select_related("hospital_staff_profile")
        .order_by("first_name", "username")
    )
    existing_profile_ids = {
        user.id for user in users if hasattr(user, "hospital_staff_profile")
    }
    StaffProfile.objects.bulk_create(
        [StaffProfile(user=user) for user in users if user.id not in existing_profile_ids],
        ignore_conflicts=True,
    )
    if len(existing_profile_ids) != len(users):
        users = list(
            get_user_model().objects
            .filter(is_active=True)
            .select_related("hospital_staff_profile")
            .order_by("first_name", "username")
        )
    duties = {
        duty.user_id: duty
        for duty in StaffDuty.objects.filter(duty_date=today, user__in=users)
    }
    staff_rows = []
    available_nurses = []
    for staff_user in users:
        duty = duties.get(staff_user.id)
        profile = staff_user.hospital_staff_profile
        available = _is_available_nurse(staff_user, duty)
        row = {
            "user": staff_user,
            "name": staff_user.get_full_name() or staff_user.username,
            "duty": duty,
            "profile": profile,
            "is_present": bool(duty and duty.is_present),
            "is_available": available,
        }
        staff_rows.append(row)
        if available:
            available_nurses.append(row)

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
            "assigned_nurse_available": _is_available_nurse(care_assignment.nurse, assigned_duty) if care_assignment else False,
        })

    return render(request, "queues/personnel_dashboard.html", {
        "today": today,
        "staff_rows": staff_rows,
        "available_nurses": available_nurses,
        "staff_role_choices": StaffProfile.Role.choices,
        "patient_rows": patient_rows,
        "present_count": sum(row["is_present"] for row in staff_rows),
        "available_count": len(available_nurses),
        "assigned_count": sum(bool(row["care_assignment"]) for row in patient_rows),
        "role_counts": {
            role: sum(row["profile"].role == role for row in staff_rows)
            for role, _label in StaffProfile.Role.choices
        },
    })
