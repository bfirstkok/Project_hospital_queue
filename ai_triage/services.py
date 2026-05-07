from django.utils import timezone
from ai_triage.rules import rule_based_triage
from ai_triage.ml.predictor import dt_predict
from queues.models import TriageResult

SEV_TO_PRIORITY = {"RED": 1, "YELLOW": 2, "GREEN": 3}


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

    if v.pr is not None and v.pr >= 120:
        reasons.append(f"PR/BPM {v.pr} >= 120")

    if v.bt is not None and v.bt >= 39:
        reasons.append(f"BT {v.bt}°C >= 39")
    elif v.bt is not None and 38 <= v.bt < 39:
        reasons.append(f"BT {v.bt}°C fever")

    return "; ".join(reasons) or "No critical vital-sign trigger detected"

def apply_ai_triage(visit):
    """
    - อ่าน vital sign
    - คำนวณ AI severity
    - บันทึกลง TriageResult
    - อัปเดต visit.final_severity + triaged_at
    - อัปเดต queue.priority
    """
    if not hasattr(visit, "vitals"):
        return None  # ไม่มี vitals

    clinical_reason = explain_vitals(visit.vitals)

    try:
        sev, conf, reason = dt_predict(visit.vitals)
        model_name = "decision_tree_v1"
    except Exception:
        sev, conf, reason = rule_based_triage(visit.vitals)
        model_name = "rule_based_fallback"

    triage_obj, _ = TriageResult.objects.get_or_create(visit=visit)
    triage_obj.ai_severity = sev
    triage_obj.model_name = model_name
    triage_obj.confidence = conf
    triage_obj.ai_reason = clinical_reason
    triage_obj.save()

    # แนะนำสีเข้า visit (พยาบาลยังแก้ได้ภายหลัง)
    visit.final_severity = sev
    visit.triaged_at = timezone.now()
    visit.save()

    if hasattr(visit, "queue"):
        visit.queue.priority = SEV_TO_PRIORITY[sev]
        visit.queue.save()

    return {"severity": sev, "confidence": conf, "reason": reason}
