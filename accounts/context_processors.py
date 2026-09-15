from .access import ROLE_DESCRIPTIONS, capabilities_for, user_role


def access_control(request):
    role = user_role(request.user)
    if role == "ADMIN":
        role_label = "ผู้ดูแลระบบสูงสุด"
        role_description = "เข้าถึงการตั้งค่า สิทธิ์ ฐานข้อมูล และหน้าทดสอบระบบ"
    else:
        profile = getattr(request.user, "hospital_staff_profile", None)
        role_label = profile.get_role_display() if profile else "เจ้าหน้าที่"
        role_description = ROLE_DESCRIPTIONS.get(role, "")
    return {
        "access_role": role,
        "access_role_label": role_label,
        "access_role_description": role_description,
        "access_capabilities": capabilities_for(request.user),
    }
