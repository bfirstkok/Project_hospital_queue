from django.contrib import messages
from django.contrib.auth import logout as auth_logout, update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import PasswordChangeForm
from django.shortcuts import render, redirect
from django.http import HttpResponseForbidden
from django.views.decorators.http import require_POST
from django.utils import timezone
from django.views.decorators.cache import never_cache


from queues.models import Queue, StaffProfile
from .access import (
    CAPABILITY_LABELS,
    Capability,
    SIMULATED_ROLE_SESSION_KEY,
    capabilities_for,
    has_capability,
    is_effective_superuser,
)


@login_required
def role_landing(request):
    """Send each account to the first page that matches its effective duty."""
    if is_effective_superuser(request.user):
        return redirect("system_test:index")
    if has_capability(request.user, Capability.DOCTOR_ASSESSMENT):
        return redirect("opd_room_select")
    if has_capability(request.user, Capability.RECORD_VITALS):
        return redirect("waiting_vitals")
    if has_capability(request.user, Capability.CONFIRM_TRIAGE):
        return redirect("waiting_confirmation")
    if has_capability(request.user, Capability.MANAGE_QUEUE):
        return redirect("queue_list")
    if has_capability(request.user, Capability.VIEW_EMERGENCY):
        return redirect("emergency_transfers")
    if has_capability(request.user, Capability.REGISTER_PATIENT):
        return redirect("register_patient")
    if has_capability(request.user, Capability.MANAGE_DEVICE):
        return redirect("device_pairing")
    if has_capability(request.user, Capability.VIEW_SHIFT_SCHEDULE):
        return redirect("shift_schedule")
    return redirect("my_permissions")


@login_required\n@require_POST\ndef switch_test_role(request):\n    """Let a real superuser temporarily simulate one staff role in this session."""\n    if not request.user.is_superuser:\n        return HttpResponseForbidden("Superuser only")\n\n    role = request.POST.get("role", "").strip()\n    if role in {"", "ADMIN"}:\n        request.session.pop(SIMULATED_ROLE_SESSION_KEY, None)\n        request.user._simulated_hospital_role = None\n        messages.success(request, "กลับสู่สิทธิ์ผู้ดูแลระบบสูงสุดแล้ว")\n    elif role in StaffProfile.Role.values:\n        request.session[SIMULATED_ROLE_SESSION_KEY] = role\n        request.user._simulated_hospital_role = role\n        label = dict(StaffProfile.Role.choices).get(role, role)\n        messages.success(request, f"กำลังทดสอบระบบในบทบาท {label}")\n    else:\n        messages.error(request, "บทบาทที่เลือกไม่ถูกต้อง")\n\n    return redirect("role_landing")\n\n\n@login_required
def my_permissions(request):
    """Explain the signed-in account's actual duties in plain language."""
    capability_values = capabilities_for(request.user)
    capability_rows = [
        {"code": code, "label": CAPABILITY_LABELS[code]}
        for code in sorted(capability_values)
    ]
    return render(request, "accounts/my_permissions.html", {"capability_rows": capability_rows})


@login_required
def account_settings(request):
    """Allow a signed-in user to securely change their own password."""
    form = PasswordChangeForm(user=request.user, data=request.POST or None)
    if request.method == "POST" and form.is_valid():
        user = form.save()
        update_session_auth_hash(request, user)
        messages.success(request, "เปลี่ยนรหัสผ่านเรียบร้อยแล้ว")
        return redirect("account_settings")
    return render(request, "accounts/account_settings.html", {"form": form})


@login_required
def dashboard(request):
    now = timezone.now()

    waiting_total = Queue.objects.filter(status="WAITING").count()
    called_total  = Queue.objects.filter(status="CALLED").count()

    # แยกสีจาก priority (RED=1, YELLOW=2, GREEN=3)
    red_total    = Queue.objects.filter(status="WAITING", priority=1).count()
    yellow_total = Queue.objects.filter(status="WAITING", priority=2).count()
    green_total  = Queue.objects.filter(status="WAITING", priority=3).count()

    return render(request, "dashboard/dashboard.html", {
        "now": now,
        "waiting_total": waiting_total,
        "called_total": called_total,
        "red_total": red_total,
        "yellow_total": yellow_total,
        "green_total": green_total,
    })


@never_cache
def custom_logout(request):
    """
    Custom logout view ที่เคลียร์ session และป้องกัน cache
    """
    auth_logout(request)
    response = redirect('login')
    # เพิ่ม headers เพื่อป้องกัน browser cache
    response['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    response['Pragma'] = 'no-cache'
    response['Expires'] = '0'
    return response
