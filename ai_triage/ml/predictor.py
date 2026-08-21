import joblib
import pandas as pd
from pathlib import Path

MODEL_PATH = Path(__file__).resolve().parents[1] / "models" / "triage_dt_v1.pkl"
_model = None

def load_model():
    global _model
    if _model is None:
        _model = joblib.load(MODEL_PATH)
    return _model

def dt_predict(v, visit=None):
    m = load_model()
    patient = getattr(visit, "patient", None)
    assessment = getattr(visit, "triage_result", None)
    mental_status_values = {
        "ALERT": 1,
        "VERBAL": 2,
        "PAIN": 3,
        "UNRESPONSIVE": 4,
    }
    x = pd.DataFrame(
        [
            {
                "rr": v.rr,
                "pr": v.pr,
                "sys_bp": v.sys_bp,
                "dia_bp": getattr(v, "dia_bp", None),
                "bt": float(v.bt) if v.bt is not None else None,
                "o2sat": v.o2sat,
                "group": getattr(v, "group", None),
                "age": getattr(patient, "age_years", None),
                "nrs_pain": getattr(v, "pain_score", None),
                "sex": getattr(v, "sex", None),
                "arrival_mode": getattr(v, "arrival_mode", None),
                "pain": getattr(v, "pain", None),
                "injury": getattr(v, "injury", None),
                "mental": getattr(v, "mental", None),
                "chief_complain": getattr(visit, "note", "") or "",
                "lifesaving_intervention": (
                    int(assessment.lifesaving_intervention)
                    if assessment and assessment.lifesaving_intervention is not None
                    else None
                ),
                "high_risk_condition": (
                    int(assessment.high_risk_condition)
                    if assessment and assessment.high_risk_condition is not None
                    else None
                ),
                "altered_mental_status": (
                    int(assessment.altered_mental_status)
                    if assessment and assessment.altered_mental_status is not None
                    else None
                ),
                "mental_status": (
                    mental_status_values.get(assessment.mental_status)
                    if assessment
                    else None
                ),
                "severe_distress": (
                    int(assessment.severe_distress)
                    if assessment and assessment.severe_distress is not None
                    else None
                ),
                "expected_resources": (
                    assessment.expected_resources if assessment else None
                ),
            }
        ]
    )
    sev = m.predict(x)[0]
    confidence = 0.70
    if hasattr(m, "predict_proba"):
        confidence = float(max(m.predict_proba(x)[0]))
    return sev, confidence, "random_forest_5level_runtime_v3"
