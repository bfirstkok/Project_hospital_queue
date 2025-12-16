import joblib
import numpy as np
from pathlib import Path

MODEL_PATH = Path("ai_triage/models/triage_dt_v1.pkl")

_model = None

def load_model():
    global _model
    if _model is None:
        _model = joblib.load(MODEL_PATH)
    return _model

def dt_predict(v):
    m = load_model()
    x = np.array([[v.rr or 0, v.pr or 0, v.sys_bp or 0, v.bt or 0.0, v.o2sat or 0]])
    sev = m.predict(x)[0]
    # DecisionTree ไม่มี prob เสมอไป แต่ขอใส่ confidence แบบง่ายก่อน
    return sev, 0.70, "decision_tree_v1"
