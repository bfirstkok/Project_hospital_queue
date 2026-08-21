from django.utils import timezone
import re

from ai_triage.rules import infer_urgent_symptoms, rule_based_triage
from ai_triage.ml.predictor import dt_predict
from queues.models import TriageResult
from queues.triage import SEVERITY_PRIORITY

SEV_TO_PRIORITY = SEVERITY_PRIORITY
URGENT_SYMPTOM_LABELS = {
    "chest_pain": "เจ็บหน้าอก",
    "dyspnea": "หายใจลำบาก / หอบเหนื่อย",
    "altered_consciousness": "ซึมลง / สับสน",
    "unresponsive": "ไม่รู้สึกตัว / เรียกไม่ตื่น",
    "seizure": "ชัก",
    "active_seizure": "กำลังชัก",
    "major_bleeding": "เลือดออกมาก",
    "severe_pain": "ปวดรุนแรง",
    "high_fever": "ไข้สูง",
    "severe_accident": "อุบัติเหตุรุนแรง",
    "stroke_signs": "อาการสงสัยโรคหลอดเลือดสมอง",
}
RISK_FLAG_LABELS = {
    "copd_asthma": "COPD / Asthma",
    "child_under_5": "เด็กอายุต่ำกว่า 5 ปี",
    "elderly_80": "ผู้สูงอายุ ≥ 80 ปี",
    "pregnant": "ตั้งครรภ์",
    "immunocompromised": "ภูมิคุ้มกันต่ำ",
}


def localize_ai_reason(reason):
    """แปลงข้อความเหตุผลรุ่นเก่าให้เหมาะกับหน้าจอภาษาไทย."""
    if not reason:
        return "ไม่พบเหตุผลประกอบ"

    replacements = [
        ("No critical vital-sign trigger detected", "ไม่พบค่าสัญญาณชีพที่เข้าเกณฑ์วิกฤต"),
        ("Rule guardrail applied", "ระบบกฎความปลอดภัยปรับระดับคำแนะนำ"),
        ("model suggested", "โมเดลแนะนำ"),
        ("rule result", "ผลจากกฎความปลอดภัย"),
        ("borderline", "อยู่ในช่วงเฝ้าระวัง"),
        ("elevated", "สูงกว่าปกติ"),
        ("fever", "มีไข้"),
        ("Pain score", "ระดับความปวด"),
        ("O2Sat", "SpO₂"),
    ]
    localized = str(reason)
    for source, target in replacements:
        localized = localized.replace(source, target)

    severity_labels = {
        "RED": "สีแดง",
        "PINK": "สีชมพู",
        "YELLOW": "สีเหลือง",
        "GREEN": "สีเขียว",
        "WHITE": "สีขาว",
    }

    def replace_guardrail(match):
        model_level = severity_labels.get(match.group(1), match.group(1))
        safe_level = severity_labels.get(match.group(2), match.group(2))
        return (
            "ระบบตรวจพบเงื่อนไขความปลอดภัย "
            f"จึงปรับคำแนะนำจาก{model_level}เป็น{safe_level}"
        )

    localized = re.sub(
        r"ระบบกฎความปลอดภัยปรับระดับคำแนะนำ "
        r"\(โมเดลแนะนำ ([A-Z]+), ผลจากกฎความปลอดภัย ([A-Z]+)\)",
        replace_guardrail,
        localized,
    )
    return localized


def explain_vitals(v):
    reasons = []

    if v.o2sat is not None and v.o2sat < 95:
        reasons.append(f"O2Sat {v.o2sat}% < 95")
    elif v.o2sat is not None and 95 <= v.o2sat <= 96:
        reasons.append(f"SpO₂ {v.o2sat}% อยู่ในช่วงเฝ้าระวัง")

    if v.rr is not None and v.rr > 30:
        reasons.append(f"RR {v.rr} > 30")
    elif v.rr is not None and 21 <= v.rr <= 30:
        reasons.append(f"อัตราการหายใจ {v.rr} ครั้ง/นาที สูงกว่าปกติ")

    if v.sys_bp is not None and v.sys_bp < 90:
        reasons.append(f"BP ตัวบน {v.sys_bp} < 90")
    elif v.sys_bp is not None and v.sys_bp >= 180:
        reasons.append(f"BP ตัวบน {v.sys_bp} >= 180")

    if v.dia_bp is not None and v.dia_bp >= 120:
        reasons.append(f"BP ตัวล่าง {v.dia_bp} >= 120")

    if v.pr is not None and v.pr >= 120:
        reasons.append(f"ชีพจร {v.pr} ครั้ง/นาที ตั้งแต่ 120")

    if v.bt is not None and v.bt >= 39:
        reasons.append(f"อุณหภูมิ {v.bt}°C ตั้งแต่ 39°C")
    elif v.bt is not None and 38 <= v.bt < 39:
        reasons.append(f"อุณหภูมิ {v.bt}°C มีไข้")

    if getattr(v, "pain_score", None) is not None and v.pain_score >= 7:
        reasons.append(f"ระดับความปวด {v.pain_score}/10")

    urgent = [URGENT_SYMPTOM_LABELS.get(x, x) for x in (getattr(v, "urgent_symptoms", None) or [])]
    if urgent:
        reasons.append("อาการเร่งด่วน: " + ", ".join(urgent))

    risks = [RISK_FLAG_LABELS.get(x, x) for x in (getattr(v, "risk_flags", None) or [])]
    if risks:
        reasons.append("กลุ่มเสี่ยง: " + ", ".join(risks))

    return "; ".join(reasons) or "ไม่พบค่าสัญญาณชีพที่เข้าเกณฑ์วิกฤต"


def explain_symptoms(symptoms_text):
    inferred = infer_urgent_symptoms(symptoms_text)
    if not inferred:
        return ""

    labels = [URGENT_SYMPTOM_LABELS.get(x, x) for x in sorted(inferred)]
    return "พบคำสำคัญจากอาการผู้ป่วย: " + ", ".join(labels)


def explain_structured_assessment(assessment):
    if not assessment:
        return ""

    reasons = []
    if assessment.lifesaving_intervention is True:
        reasons.append("พยาบาลระบุว่าต้องช่วยชีวิตทันที")
    if assessment.high_risk_condition is True:
        reasons.append("พยาบาลระบุภาวะเสี่ยงสูง")
    mental_labels = {
        "ALERT": "รู้สึกตัวดี",
        "VERBAL": "ตอบสนองต่อเสียงเรียก",
        "PAIN": "ตอบสนองเมื่อกระตุ้นด้วยความเจ็บปวด",
        "UNRESPONSIVE": "ไม่ตอบสนอง",
    }
    if assessment.mental_status in mental_labels:
        reasons.append(f"ระดับการตอบสนอง: {mental_labels[assessment.mental_status]}")
    if assessment.severe_distress is True:
        reasons.append("พยาบาลประเมินว่ามีอาการรุนแรงหรือทุกข์ทรมานมาก")

    resource_labels = {
        "0": "คาดว่าไม่ใช้ทรัพยากรเพิ่มเติม",
        "1": "คาดว่าใช้ทรัพยากร 1 รายการ",
        "2_PLUS": "คาดว่าใช้ทรัพยากรมากกว่า 1 รายการ",
    }
    if assessment.expected_resources in resource_labels:
        reasons.append(resource_labels[assessment.expected_resources])
    return "; ".join(reasons)

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
    assessment = getattr(visit, "triage_result", None)
    rule_sev, rule_conf, rule_reason = rule_based_triage(
        visit.vitals,
        symptoms_text=symptoms_text,
        assessment=assessment,
    )
    clinical_reason = explain_vitals(visit.vitals)
    symptom_reason = explain_symptoms(symptoms_text)
    if symptom_reason:
        clinical_reason = f"{clinical_reason}; {symptom_reason}"
    assessment_reason = explain_structured_assessment(assessment)
    if assessment_reason:
        clinical_reason = f"{clinical_reason}; {assessment_reason}"

    try:
        model_sev, model_conf, model_reason = dt_predict(visit.vitals, visit=visit)
        model_name = f"{model_reason}_guarded_by_rules"
    except Exception:
        model_sev, model_conf, model_reason = rule_sev, rule_conf, rule_reason
        model_name = "rule_based_fallback"

    # Rules are the safety floor for RED/PINK/YELLOW. When no warning rule
    # fires, the five-level model may distinguish GREEN from WHITE. A model
    # alone cannot escalate a clinically normal case into the emergency lane.
    if rule_sev == "GREEN" and model_sev in {"GREEN", "WHITE"}:
        sev = model_sev
        conf = model_conf
        reason = model_reason
    else:
        sev = rule_sev
        conf = rule_conf
        reason = rule_reason
    if model_sev != rule_sev and sev == rule_sev:
        clinical_reason = (
            f"{clinical_reason}; ระบบกฎความปลอดภัยปรับระดับคำแนะนำ "
            f"(โมเดลแนะนำ {model_sev}, ผลจากกฎความปลอดภัย {rule_sev})"
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
