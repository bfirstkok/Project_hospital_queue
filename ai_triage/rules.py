def rule_based_triage(v):
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
    risk_flags = set(getattr(v, "risk_flags", None) or [])

    reasons = []

    # RED (ฉุกเฉิน) - ใช้เกณฑ์ที่ชัด/อธิบายง่าย
    if o2 is not None and o2 < 95:
        reasons.append("O2Sat < 95")
    if rr is not None and rr > 30:
        reasons.append("RR > 30")
    if sys_bp is not None and sys_bp < 90:
        reasons.append("SYS BP < 90")
    if bt is not None and bt >= 39:
        reasons.append("BT >= 39")
    if pain_score is not None and pain_score >= 7:
        reasons.append(f"Pain score {pain_score} >= 7")

    red_symptoms = {
        "chest_pain": "Chest pain",
        "dyspnea": "Dyspnea",
        "altered_consciousness": "Altered consciousness",
        "seizure": "Seizure",
        "major_bleeding": "Major bleeding",
        "severe_pain": "Severe pain",
        "severe_accident": "Severe accident",
    }
    for key, label in red_symptoms.items():
        if key in urgent_symptoms:
            reasons.append(label)

    if reasons:
        return ("RED", 0.90, ", ".join(reasons))

    # YELLOW (เร่งด่วน)
    y = []
    if o2 is not None and 95 <= o2 <= 96:
        y.append("O2Sat 95-96")
    if rr is not None and 21 <= rr <= 30:
        y.append("RR 21-30")
    if pr is not None and pr >= 120:
        y.append("PR >= 120")
    if bt is not None and 38 <= bt < 39:
        y.append("BT 38-38.9")
    if "high_fever" in urgent_symptoms:
        y.append("High fever symptom")

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

    # GREEN (ทั่วไป)
    return ("GREEN", 0.60, "No danger signs")
