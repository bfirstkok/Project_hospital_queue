import joblib
import numpy as np
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
    x = np.array([[v.rr or 0, v.pr or 0, v.sys_bp or 0, float(v.bt or 0.0), v.o2sat or 0]])
    sev = m.predict(x)[0]
    confidence = 0.70
    if hasattr(m, "predict_proba"):
        confidence = float(np.max(m.predict_proba(x)[0]))
    return sev, confidence, "decision_tree_v1"
