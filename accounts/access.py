from functools import wraps

from django.contrib.auth.views import redirect_to_login
from django.shortcuts import render

from queues.models import StaffProfile


class Capability:
    VIEW_DASHBOARD = "view_dashboard"
    VIEW_QUEUE = "view_queue"
    MANAGE_QUEUE = "manage_queue"
    REGISTER_PATIENT = "register_patient"
    VIEW_PATIENT = "view_patient"
    EDIT_PATIENT = "edit_patient"
    RECORD_VITALS = "record_vitals"
    CONFIRM_TRIAGE = "confirm_triage"
    DOCTOR_ASSESSMENT = "doctor_assessment"
    MONITOR_PATIENT = "monitor_patient"
    ACKNOWLEDGE_ALERT = "acknowledge_alert"
    VIEW_PERSONNEL = "view_personnel"
    MANAGE_PERSONNEL = "manage_personnel"
    MANAGE_DEVICE = "manage_device"
    VIEW_REPORT = "view_report"
    SYSTEM_TEST = "system_test"


ROLE_CAPABILITIES = {
    StaffProfile.Role.DOCTOR: {
        Capability.DOCTOR_ASSESSMENT,
    },
    StaffProfile.Role.NURSE: {
        Capability.VIEW_QUEUE,
        Capability.MANAGE_QUEUE,
        Capability.REGISTER_PATIENT,
        Capability.VIEW_PATIENT,
        Capability.EDIT_PATIENT,
        Capability.RECORD_VITALS,
        Capability.CONFIRM_TRIAGE,
        Capability.MONITOR_PATIENT,
        Capability.ACKNOWLEDGE_ALERT,
        Capability.VIEW_PERSONNEL,
        Capability.MANAGE_DEVICE,
    },
    StaffProfile.Role.NURSE_ASSISTANT: {
        Capability.REGISTER_PATIENT,
        Capability.VIEW_PATIENT,
        Capability.EDIT_PATIENT,
        Capability.RECORD_VITALS,
    },
    StaffProfile.Role.EMERGENCY: {
        Capability.VIEW_QUEUE,
        Capability.MANAGE_QUEUE,
        Capability.VIEW_PATIENT,
        Capability.MONITOR_PATIENT,
        Capability.ACKNOWLEDGE_ALERT,
    },
    StaffProfile.Role.STAFF: {
        Capability.VIEW_QUEUE,
        Capability.MANAGE_QUEUE,
        Capability.REGISTER_PATIENT,
        Capability.VIEW_PATIENT,
        Capability.EDIT_PATIENT,
    },
}


ROLE_DESCRIPTIONS = {
    StaffProfile.Role.DOCTOR: "ตรวจรักษา บันทึกผลประเมิน และดูข้อมูลผู้ป่วยที่เกี่ยวข้อง",
    StaffProfile.Role.NURSE: "วัดสัญญาณชีพ ยืนยันผลคัดกรอง จัดคิว และติดตามผู้ป่วย",
    StaffProfile.Role.NURSE_ASSISTANT: "ลงทะเบียน ช่วยวัดสัญญาณชีพ และดูหน้าติดตาม",
    StaffProfile.Role.EMERGENCY: "ดูคิวฉุกเฉิน ส่งต่อ และตอบรับสัญญาณเตือน",
    StaffProfile.Role.STAFF: "ลงทะเบียน ค้นหาผู้ป่วย เรียกคิว และจัดการนัดหมาย",
}

CAPABILITY_LABELS = {
    Capability.VIEW_DASHBOARD: "ดูแดชบอร์ดภาพรวม",
    Capability.VIEW_QUEUE: "ดูรายการคิว",
    Capability.MANAGE_QUEUE: "เรียกคิวและเปลี่ยนสถานะบริการ",
    Capability.REGISTER_PATIENT: "ลงทะเบียนผู้ป่วย",
    Capability.VIEW_PATIENT: "ค้นหาและดูประวัติผู้ป่วย",
    Capability.EDIT_PATIENT: "แก้ไขข้อมูลและนัดหมาย",
    Capability.RECORD_VITALS: "วัดและบันทึกสัญญาณชีพ",
    Capability.CONFIRM_TRIAGE: "ยืนยันหรือแก้ผลคัดกรอง AI",
    Capability.DOCTOR_ASSESSMENT: "ตรวจรักษาและบันทึกผลแพทย์",
    Capability.MONITOR_PATIENT: "ติดตามข้อมูลจากอุปกรณ์",
    Capability.ACKNOWLEDGE_ALERT: "รับทราบสัญญาณเตือน",
    Capability.VIEW_PERSONNEL: "ดูรายชื่อและสถานะบุคลากร",
    Capability.MANAGE_PERSONNEL: "จัดบทบาท เวร และมอบหมายพยาบาล",
    Capability.MANAGE_DEVICE: "สร้างและจับคู่อุปกรณ์",
    Capability.VIEW_REPORT: "ดูและส่งออกรายงาน",
    Capability.SYSTEM_TEST: "ทดสอบระบบและดูฐานข้อมูล",
}


def user_role(user):
    if not getattr(user, "is_authenticated", False):
        return None
    if user.is_superuser:
        return "ADMIN"
    profile = getattr(user, "hospital_staff_profile", None)
    return profile.role if profile else StaffProfile.Role.STAFF


def capabilities_for(user):
    if not getattr(user, "is_authenticated", False):
        return set()
    if user.is_superuser:
        return {
            value
            for name, value in vars(Capability).items()
            if name.isupper() and isinstance(value, str)
        }
    return set(ROLE_CAPABILITIES.get(user_role(user), set()))


def has_capability(user, capability):
    return capability in capabilities_for(user)


def capability_required(capability):
    """Require one hospital capability and return a useful Thai 403 page."""

    def decorator(view_func):
        @wraps(view_func)
        def wrapped(request, *args, **kwargs):
            if not request.user.is_authenticated:
                return redirect_to_login(request.get_full_path())
            if not has_capability(request.user, capability):
                return render(
                    request,
                    "403.html",
                    {"required_capability": capability, "current_role": user_role(request.user)},
                    status=403,
                )
            return view_func(request, *args, **kwargs)

        wrapped.required_capability = capability
        return wrapped

    return decorator


def superuser_required(view_func):
    @wraps(view_func)
    def wrapped(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect_to_login(request.get_full_path())
        if not request.user.is_superuser:
            return render(
                request,
                "403.html",
                {"required_capability": Capability.SYSTEM_TEST, "current_role": user_role(request.user)},
                status=403,
            )
        return view_func(request, *args, **kwargs)

    wrapped.required_capability = Capability.SYSTEM_TEST
    return wrapped
