import os, joblib
import numpy as np
import pandas as pd
from sklearn.model_selection import train_test_split
from sklearn.tree import DecisionTreeClassifier
from sklearn.metrics import classification_report, confusion_matrix, accuracy_score

SEV = ["GREEN", "YELLOW", "RED"]

def label_rule(rr, pr, sys_bp, bt, o2):
    # RED
    if o2 < 95 or rr > 30 or sys_bp < 90 or bt >= 39:
        return "RED"
    # YELLOW
    if (95 <= o2 <= 96) or (21 <= rr <= 30) or (pr >= 120) or (38 <= bt < 39):
        return "YELLOW"
    return "GREEN"

def make_synth(n=2000, seed=42):
    rng = np.random.default_rng(seed)
    rr = rng.integers(12, 40, size=n)
    pr = rng.integers(60, 140, size=n)
    sys_bp = rng.integers(80, 160, size=n)
    bt = rng.uniform(36.0, 40.5, size=n).round(1)
    o2 = rng.integers(90, 100, size=n)

    y = [label_rule(rr[i], pr[i], sys_bp[i], bt[i], o2[i]) for i in range(n)]
    df = pd.DataFrame({"rr": rr, "pr": pr, "sys_bp": sys_bp, "bt": bt, "o2sat": o2, "label": y})
    return df

def main():
    df = make_synth()
    X = df[["rr", "pr", "sys_bp", "bt", "o2sat"]]
    y = df["label"]

    X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, random_state=42, stratify=y)

    clf = DecisionTreeClassifier(max_depth=4, random_state=42)
    clf.fit(X_train, y_train)

    pred = clf.predict(X_test)
    acc = accuracy_score(y_test, pred)
    print("Accuracy:", acc)
    print("Confusion Matrix:\n", confusion_matrix(y_test, pred, labels=SEV))
    print(classification_report(y_test, pred))

    os.makedirs("ai_triage/models", exist_ok=True)
    joblib.dump(clf, "ai_triage/models/triage_dt_v1.pkl")
    print("Saved: ai_triage/models/triage_dt_v1.pkl")

if __name__ == "__main__":
    main()
