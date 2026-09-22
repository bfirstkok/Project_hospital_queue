from __future__ import annotations

from django.utils import timezone

from .models import ConfirmedTriageCase, TriageResult, Visit


VALID_SEVERITIES = set(Visit.Severity.values)


def _training_eligibility(triage_result, vitals):
    if not triage_result or triage_result.nurse_severity not in VALID_SEVERITIES:
        return False, "ยังไม่มีผลยืนยันจากพยาบาล"
    if vitals is None:
        return False, "ไม่มีสัญญาณชีพ"
    required = (
        "lifesaving_intervention",
        "high_risk_condition",
        "altered_mental_status",
        "mental_status",
        "severe_distress",
        "expected_resources",
    )
    missing = [name for name in required if getattr(triage_result, name, None) in (None, "")]
    if missing:
        return False, "ข้อมูล structured triage ยังไม่ครบ: " + ", ".join(missing)
    return True, "พร้อมใช้เป็นข้อมูลฝึกโมเดล"


def capture_confirmed_triage_case(*, visit, triage_result=None, actor=None):
    """Create/update one stable feature snapshot for the latest nurse confirmation."""
    visit = (
        Visit.objects
        .select_related("patient", "vitals", "triage_result")
        .get(pk=visit.pk)
    )
    triage_result = triage_result or getattr(visit, "triage_result", None)
    vitals = getattr(visit, "vitals", None)
    eligible, eligibility_note = _training_eligibility(triage_result, vitals)

    mental = getattr(triage_result, "mental_status", None)
    values = {
        "confirmed_by": actor if actor and getattr(actor, "is_authenticated", False) else None,
        "confirmed_at": visit.confirmed_at or timezone.now(),
        "age": getattr(visit.patient, "age_years", None),
        "nrs_pain": getattr(vitals, "pain_score", None),
        "rr": getattr(vitals, "rr", None),
        "pr": getattr(vitals, "pr", None),
        "sys_bp": getattr(vitals, "sys_bp", None),
        "dia_bp": getattr(vitals, "dia_bp", None),
        "bt": getattr(vitals, "bt", None),
        "o2sat": getattr(vitals, "o2sat", None),
        "chief_complain": visit.note or "",
        "urgent_symptoms": list(getattr(vitals, "urgent_symptoms", None) or []),
        "risk_flags": list(getattr(vitals, "risk_flags", None) or []),
        "lifesaving_intervention": getattr(triage_result, "lifesaving_intervention", None),
        "high_risk_condition": getattr(triage_result, "high_risk_condition", None),
        "altered_mental_status": getattr(triage_result, "altered_mental_status", None),
        "mental_status": mental,
        "severe_distress": getattr(triage_result, "severe_distress", None),
        "expected_resources": getattr(triage_result, "expected_resources", None),
        "ai_severity": getattr(triage_result, "ai_severity", None),
        "nurse_severity": getattr(triage_result, "nurse_severity", None) or visit.final_severity,
        "model_name": getattr(triage_result, "model_name", "") or "",
        "confidence": getattr(triage_result, "confidence", None),
        "ai_reason": getattr(triage_result, "ai_reason", "") or "",
        "nurse_note": getattr(triage_result, "nurse_note", "") or "",
        "is_ai_match": bool(
            getattr(triage_result, "ai_severity", None)
            and getattr(triage_result, "nurse_severity", None)
            and triage_result.ai_severity == triage_result.nurse_severity
        ),
        "is_training_eligible": eligible,
        "eligibility_note": eligibility_note,
        "snapshot_version": ConfirmedTriageCase.SNAPSHOT_VERSION,
    }
    case, _ = ConfirmedTriageCase.objects.update_or_create(
        visit=visit,
        defaults=values,
    )
    return case
