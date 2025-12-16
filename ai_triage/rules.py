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

    if y:
        return ("YELLOW", 0.75, ", ".join(y))

    # GREEN (ทั่วไป)
    return ("GREEN", 0.60, "No danger signs")

def rule_based_triage(v):
    rr = v.rr
    pr = v.pr
    sys_bp = v.sys_bp
    bt = v.bt
    o2 = v.o2sat

    reasons = []

    # RED (ฉุกเฉิน)
    if o2 is not None and o2 < 95:
        reasons.append("O2Sat < 95")
    if rr is not None and rr > 30:
        reasons.append("RR > 30")
    if sys_bp is not None and sys_bp < 90:
        reasons.append("SYS BP < 90")
    if bt is not None and bt >= 39:
        reasons.append("BT >= 39")

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

    if y:
        return ("YELLOW", 0.75, ", ".join(y))

    # GREEN
    return ("GREEN", 0.60, "No danger signs")
