from django.utils import timezone
from ai_triage.rules import rule_based_triage
from queues.models import TriageResult

SEV_TO_PRIORITY = {"RED": 1, "YELLOW": 2, "GREEN": 3}

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

    sev, conf, reason = rule_based_triage(visit.vitals)

    triage_obj, _ = TriageResult.objects.get_or_create(visit=visit)
    triage_obj.ai_severity = sev
    triage_obj.model_name = "rule_based_v1"
    triage_obj.confidence = conf
    triage_obj.save()

    # แนะนำสีเข้า visit (พยาบาลยังแก้ได้ภายหลัง)
    visit.final_severity = sev
    visit.triaged_at = timezone.now()
    visit.save()

    if hasattr(visit, "queue"):
        visit.queue.priority = SEV_TO_PRIORITY[sev]
        visit.queue.save()

    return {"severity": sev, "confidence": conf, "reason": reason}
