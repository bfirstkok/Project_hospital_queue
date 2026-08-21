SYMPTOM_KEYWORDS = {
    "chest_pain": ["เจ็บหน้าอก", "แน่นหน้าอก", "ปวดหน้าอก"],
    "dyspnea": ["หอบ", "เหนื่อยหอบ", "หายใจลำบาก", "หายใจไม่ออก"],
    "altered_consciousness": ["ซึม", "สับสน", "ตอบไม่รู้เรื่อง"],
    "unresponsive": ["หมดสติ", "ไม่รู้สึกตัว", "เรียกไม่ตื่น"],
    "seizure": ["ชัก", "เกร็งกระตุก"],
    "active_seizure": ["กำลังชัก", "ชักไม่หยุด"],
    "major_bleeding": ["เลือดออกมาก", "เลือดไหลไม่หยุด", "เสียเลือด"],
    "severe_pain": ["ปวดรุนแรง", "ปวดมาก", "ปวดที่สุด", "เจ็บมาก"],
    "high_fever": ["ไข้สูง", "ตัวร้อนมาก"],
    "severe_accident": ["อุบัติเหตุรุนแรง", "รถชน", "ตกจากที่สูง"],
    "stroke_signs": ["หน้าเบี้ยว", "แขนขาอ่อนแรง", "พูดไม่ชัด"],
}

NEGATION_MARKERS = (
    "ไม่มีอาการ",
    "ไม่พบอาการ",
    "ปฏิเสธอาการ",
    "ไม่มี",
    "ไม่พบ",
    "ไม่ได้มี",
    "ปฏิเสธ",
    "ไม่",
)
CRITICAL_O2SAT = 90
CRITICAL_RR = 35
CRITICAL_SYS_BP = 80


def _keyword_is_negated(text, keyword):
    """Ignore urgent keywords that are immediately preceded by a Thai negation."""
    start = text.find(keyword)
    while start >= 0:
        prefix = text[max(0, start - 14):start]
        if not any(prefix.endswith(marker) for marker in NEGATION_MARKERS):
            return False
        start = text.find(keyword, start + len(keyword))
    return True


def infer_urgent_symptoms(symptoms_text):
    text = (symptoms_text or "").strip().lower()
    if not text:
        return set()

    inferred = set()
    for symptom, keywords in SYMPTOM_KEYWORDS.items():
        if any(keyword in text and not _keyword_is_negated(text, keyword) for keyword in keywords):
            inferred.add(symptom)
    return inferred


def rule_based_triage(v, symptoms_text="", assessment=None):
    """
    v = VitalSign instance (rr, pr, sys_bp, dia_bp, bt, o2sat)
    return: (severity, confidence, reason)
    """
    rr = v.rr
    pr = v.pr
    sys_bp = v.sys_bp
    bt = v.bt
    o2 = v.o2sat
    pain_score = getattr(v, "pain_score", None)
    urgent_symptoms = set(getattr(v, "urgent_symptoms", None) or [])
    urgent_symptoms.update(infer_urgent_symptoms(symptoms_text))
    risk_flags = set(getattr(v, "risk_flags", None) or [])

    reasons = []

    # LEVEL 1 / RED: ต้องการการช่วยชีวิตทันที ไม่ใช่เพียง "ผิดปกติ"
    if getattr(assessment, "lifesaving_intervention", None) is True:
        reasons.append("Nurse assessment: immediate lifesaving intervention required")
    if getattr(assessment, "mental_status", None) == "UNRESPONSIVE":
        reasons.append("Nurse assessment: unresponsive")
    if o2 is not None and o2 < CRITICAL_O2SAT:
        reasons.append(f"O2Sat < {CRITICAL_O2SAT}")
    if rr is not None and (rr <= 6 or rr >= CRITICAL_RR):
        reasons.append(f"RR <= 6 or >= {CRITICAL_RR}")
    if sys_bp is not None and sys_bp < CRITICAL_SYS_BP:
        reasons.append(f"BP ตัวบน < {CRITICAL_SYS_BP}")

    immediate_symptoms = {
        "unresponsive": "Unresponsive",
        "active_seizure": "Active seizure",
        "major_bleeding": "Major bleeding",
    }
    for key, label in immediate_symptoms.items():
        if key in urgent_symptoms:
            reasons.append(label)

    if reasons:
        return ("RED", 0.95, ", ".join(reasons))

    # LEVEL 2 / PINK: high-risk, altered mental state, severe pain with
    # supporting clinical signs, or dangerous vital signs. These patients are
    # routed for rapid emergency assessment but are not labelled resuscitation.
    pink = []
    if getattr(assessment, "high_risk_condition", None) is True:
        pink.append("Nurse assessment: high-risk condition")
    if getattr(assessment, "altered_mental_status", None) is True:
        pink.append("Nurse assessment: altered mental status")
    if getattr(assessment, "mental_status", None) in {"VERBAL", "PAIN"}:
        pink.append("Nurse assessment: reduced mental response")
    if getattr(assessment, "severe_distress", None) is True:
        pink.append("Nurse assessment: severe distress")
    high_risk_symptoms = {
        "chest_pain": "Chest pain / possible ACS",
        "altered_consciousness": "New altered consciousness",
        "seizure": "Seizure",
        "severe_accident": "Severe accident",
        "stroke_signs": "Possible stroke signs",
    }
    for key, label in high_risk_symptoms.items():
        if key in urgent_symptoms:
            pink.append(label)

    respiratory_warning = (
        (o2 is not None and o2 < 95)
        or (rr is not None and rr > 30)
    )
    if "dyspnea" in urgent_symptoms and respiratory_warning:
        pink.append("Dyspnea with abnormal respiratory vital sign")

    danger_vitals = []
    if o2 is not None and o2 < 95:
        danger_vitals.append("O2Sat < 95")
    if rr is not None and rr > 30:
        danger_vitals.append("RR > 30")
    if sys_bp is not None and sys_bp < 90:
        danger_vitals.append("BP ตัวบน < 90")
    # Tachycardia alone is a warning feature (level 3), not an automatic
    # emergency transfer. It can support level 2 when severe pain is present.
    if danger_vitals:
        pink.append("Danger vital signs: " + ", ".join(danger_vitals))

    severe_pain = (
        (pain_score is not None and pain_score >= 7)
        or "severe_pain" in urgent_symptoms
    )
    supporting_pain_vitals = list(danger_vitals)
    if pr is not None and pr >= 120:
        supporting_pain_vitals.append("PR >= 120")
    if severe_pain and supporting_pain_vitals:
        pink.append("Severe pain with abnormal vital sign")

    if pink:
        return ("PINK", 0.85, ", ".join(dict.fromkeys(pink)))

    # LEVEL 3 / YELLOW: urgent/observation. A single risk flag or warning
    # feature is not automatically an emergency transfer.
    y = []
    if o2 is not None and 95 <= o2 <= 96:
        y.append("O2Sat 95-96")
    if rr is not None and 21 <= rr <= 30:
        y.append("RR 21-30")
    if pr is not None and pr >= 120:
        y.append("PR >= 120")
    if bt is not None and bt >= 39:
        y.append("BT >= 39")
    elif bt is not None and 38 <= bt < 39:
        y.append("BT 38-38.9")
    if pain_score is not None and pain_score >= 7:
        y.append(f"Pain score {pain_score} >= 7")
    if "severe_pain" in urgent_symptoms:
        y.append("Severe pain")
    if "dyspnea" in urgent_symptoms:
        y.append("Dyspnea")
    if "high_fever" in urgent_symptoms:
        y.append("High fever symptom")
    if sys_bp is not None and sys_bp >= 180:
        y.append("BP ตัวบน >= 180")
    if v.dia_bp is not None and v.dia_bp >= 120:
        y.append("BP ตัวล่าง >= 120")

    yellow_risks = {
        "copd_asthma": "COPD/Asthma",
        "child_under_5": "Child under 5",
        "elderly_80": "Age >= 80",
        "pregnant": "Pregnant",
        "immunocompromised": "Low immunity",
    }
    for key, label in yellow_risks.items():
        if key in risk_flags:
            y.append(label)

    if y:
        return ("YELLOW", 0.75, ", ".join(y))

    # When level 1-2 decision points are negative, expected resources are the
    # structured discriminator for levels 3-5. This field is supplied by the
    # nurse and is never inferred from a single vital sign.
    expected_resources = getattr(assessment, "expected_resources", None)
    if expected_resources == "2_PLUS":
        return ("YELLOW", 0.75, "Expected medical resources > 1")
    if expected_resources == "1":
        return ("GREEN", 0.70, "Expected medical resources = 1")
    if expected_resources == "0":
        return ("WHITE", 0.70, "Expected medical resources = 0")

    # LEVEL 4 / GREEN is the rule default. The ML model may downgrade a
    # clinically normal case to LEVEL 5 / WHITE when trained on five labels.
    return ("GREEN", 0.60, "No danger signs")
