from queues.room_assignment import active_doctor_room_assignment
from queues.models import StaffProfile

from .access import ROLE_DESCRIPTIONS, capabilities_for, simulated_role_for, user_role


def access_control(request):
    role = user_role(request.user)
    profile = getattr(request.user, "hospital_staff_profile", None)
    simulated_role = simulated_role_for(request.user)
    role_labels = dict(StaffProfile.Role.choices)

    if role == "ADMIN":
        role_label = "ผู้ดูแลระบบสูงสุด"
        role_description = "เข้าถึงการตั้งค่า สิทธิ์ ฐานข้อมูล และหน้าทดสอบระบบ"
    elif simulated_role:
        role_label = f"โหมดทดสอบ: {role_labels.get(role, role)}"
        role_description = ROLE_DESCRIPTIONS.get(role, "")
    else:
        role_label = profile.get_role_display() if profile else role_labels.get(role, "เจ้าหน้าที่")
        role_description = ROLE_DESCRIPTIONS.get(role, "")

    assignment = active_doctor_room_assignment(request.user)
    return {
        "access_role": role,
        "access_role_label": role_label,
        "access_role_description": role_description,
        "access_capabilities": capabilities_for(request.user),
        "access_profile": profile,
        "access_assigned_exam_room": assignment.room if assignment else None,
        "access_active_shift": assignment.shift if assignment else None,
        "access_duty_label": assignment.duty_label if assignment else "",
        "role_simulation_active": bool(simulated_role),
        "role_simulation_value": simulated_role or "ADMIN",
        "admin_role_choices": StaffProfile.Role.choices if request.user.is_authenticated and request.user.is_superuser else (),
    }
