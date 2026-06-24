from django.utils import timezone
from ai_triage.rules import infer_urgent_symptoms, rule_based_triage
from ai_triage.ml.predictor import dt_predict
from queues.models import TriageResult

SEV_TO_PRIORITY = {"RED": 1, "YELLOW": 2, "GREEN": 3}
URGENT_SYMPTOM_LABELS = {
    "chest_pain": "เจ็บหน้าอก",
    "dyspnea": "หายใจลำบาก / หอบเหนื่อย",
    "altered_consciousness": "ซึมลง / หมดสติ",
    "seizure": "ชัก",
    "major_bleeding": "เลือดออกมาก",
    "severe_pain": "ปวดรุนแรง",
    "high_fever": "ไข้สูง",
    "severe_accident": "อุบัติเหตุรุนแรง",
}
RISK_FLAG_LABELS = {
    "copd_asthma": "COPD / Asthma",
    "child_under_5": "เด็กอายุต่ำกว่า 5 ปี",
    "elderly_80": "ผู้สูงอายุ ≥ 80 ปี",
    "pregnant": "ตั้งครรภ์",
    "immunocompromised": "ภูมิคุ้มกันต่ำ",
}


def explain_vitals(v):
    reasons = []

    if v.o2sat is not None and v.o2sat < 95:
        reasons.append(f"O2Sat {v.o2sat}% < 95")
    elif v.o2sat is not None and 95 <= v.o2sat <= 96:
        reasons.append(f"O2Sat {v.o2sat}% borderline")

    if v.rr is not None and v.rr > 30:
        reasons.append(f"RR {v.rr} > 30")
    elif v.rr is not None and 21 <= v.rr <= 30:
        reasons.append(f"RR {v.rr} elevated")

    if v.sys_bp is not None and v.sys_bp < 90:
        reasons.append(f"Systolic BP {v.sys_bp} < 90")
    elif v.sys_bp is not None and v.sys_bp >= 180:
        reasons.append(f"Systolic BP {v.sys_bp} >= 180")

    if v.dia_bp is not None and v.dia_bp >= 120:
        reasons.append(f"Diastolic BP {v.dia_bp} >= 120")

    if v.pr is not None and v.pr >= 120:
        reasons.append(f"PR/BPM {v.pr} >= 120")

    if v.bt is not None and v.bt >= 39:
        reasons.append(f"BT {v.bt}°C >= 39")
    elif v.bt is not None and 38 <= v.bt < 39:
        reasons.append(f"BT {v.bt}°C fever")

    if getattr(v, "pain_score", None) is not None and v.pain_score >= 7:
        reasons.append(f"Pain score {v.pain_score}/10")

    urgent = [URGENT_SYMPTOM_LABELS.get(x, x) for x in (getattr(v, "urgent_symptoms", None) or [])]
    if urgent:
        reasons.append("อาการเร่งด่วน: " + ", ".join(urgent))

    risks = [RISK_FLAG_LABELS.get(x, x) for x in (getattr(v, "risk_flags", None) or [])]
    if risks:
        reasons.append("กลุ่มเสี่ยง: " + ", ".join(risks))

    return "; ".join(reasons) or "No critical vital-sign trigger detected"


def explain_symptoms(symptoms_text):
    inferred = infer_urgent_symptoms(symptoms_text)
    if not inferred:
        return ""

    labels = [URGENT_SYMPTOM_LABELS.get(x, x) for x in sorted(inferred)]
    return "พบคำสำคัญจากอาการผู้ป่วย: " + ", ".join(labels)

def apply_ai_triage(visit):
    """
    - อ่าน vital sign
    - คำนวณ AI severity recommendation
    - บันทึกลง TriageResult
    - ไม่อัปเดต final_severity อัตโนมัติ เพราะต้องให้พยาบาลยืนยัน
    """
    if not hasattr(visit, "vitals"):
        return None  # ไม่มี vitals

    symptoms_text = getattr(visit, "note", "") or ""
    rule_sev, rule_conf, rule_reason = rule_based_triage(visit.vitals, symptoms_text=symptoms_text)
    clinical_reason = explain_vitals(visit.vitals)
    symptom_reason = explain_symptoms(symptoms_text)
    if symptom_reason:
        clinical_reason = f"{clinical_reason}; {symptom_reason}"

    try:
        model_sev, model_conf, model_reason = dt_predict(visit.vitals)
        model_name = f"{model_reason}_guarded_by_rules"
    except Exception:
        model_sev, model_conf, model_reason = rule_sev, rule_conf, rule_reason
        model_name = "rule_based_fallback"

    sev = rule_sev
    conf = rule_conf
    reason = rule_reason
    if model_sev != rule_sev:
        clinical_reason = (
            f"{clinical_reason}; Rule guardrail applied "
            f"(model suggested {model_sev}, rule result {rule_sev})"
        )

    triage_obj, _ = TriageResult.objects.get_or_create(visit=visit)
    triage_obj.ai_severity = sev
    triage_obj.model_name = model_name
    triage_obj.confidence = conf
    triage_obj.ai_reason = clinical_reason
    triage_obj.save()

    visit.triaged_at = timezone.now()
    visit.save(update_fields=["triaged_at"])

    return {"severity": sev, "confidence": conf, "reason": reason}
