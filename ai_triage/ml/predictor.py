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

def dt_predict(v):
    m = load_model()
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
                "age": getattr(v, "age", None),
                "patients_number_per_hour": getattr(v, "patients_number_per_hour", None),
                "nrs_pain": getattr(v, "nrs_pain", None),
                "sex": getattr(v, "sex", None),
                "arrival_mode": getattr(v, "arrival_mode", None),
                "pain": getattr(v, "pain", None),
                "injury": getattr(v, "injury", None),
                "mental": getattr(v, "mental", None),
                "chief_complain": getattr(v, "chief_complain", "") or "",
            }
        ]
    )
    sev = m.predict(x)[0]
    confidence = 0.70
    if hasattr(m, "predict_proba"):
        confidence = float(max(m.predict_proba(x)[0]))
    return sev, confidence, "random_forest_v1"
