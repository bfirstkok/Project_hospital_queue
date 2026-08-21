"""Shared five-level triage metadata and operational routing groups.

Clinical level and service destination are intentionally separate. AI may
recommend a level, but only a nurse-confirmed level is allowed to route a
visit.
"""

SEVERITY_LEVELS = ("RED", "PINK", "YELLOW", "GREEN", "WHITE")

SEVERITY_PRIORITY = {
    "RED": 1,
    "PINK": 2,
    "YELLOW": 3,
    "GREEN": 4,
    "WHITE": 5,
}

SEVERITY_LABELS_TH = {
    "RED": "สีแดง · วิกฤต",
    "PINK": "สีชมพู · ฉุกเฉิน",
    "YELLOW": "สีเหลือง · เร่งด่วน",
    "GREEN": "สีเขียว · ไม่เร่งด่วน",
    "WHITE": "สีขาว · ผู้ป่วยทั่วไป",
}

SEVERITY_ACTIONS_TH = {
    "RED": "ช่วยเหลือทันทีและส่งต่อฉุกเฉิน",
    "PINK": "ประเมินฉุกเฉินอย่างรวดเร็ว",
    "YELLOW": "กลุ่มเฝ้าระวังและพิจารณาอุปกรณ์",
    "GREEN": "เข้าคิว OPD ไม่เร่งด่วน",
    "WHITE": "เข้ารับบริการ OPD ตามลำดับ",
}

EMERGENCY_SEVERITIES = frozenset({"RED", "PINK"})
WEARABLE_SEVERITIES = frozenset({"YELLOW"})
OPD_QUEUE_SEVERITIES = frozenset({"GREEN", "WHITE"})


def priority_for(severity):
    return SEVERITY_PRIORITY.get(severity, 5)
