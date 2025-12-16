import os
import joblib
import numpy as np
import pandas as pd

from sklearn.model_selection import train_test_split
from sklearn.tree import DecisionTreeClassifier
from sklearn.metrics import (
    accuracy_score,
    confusion_matrix,
    classification_report
)

LABELS = ["GREEN", "YELLOW", "RED"]  # กำหนดลำดับไว้ให้ตาราง Confusion Matrix คงที่

def label_rule(rr, pr, sys_bp, bt, o2):
    # RED (ฉุกเฉิน)
    if (o2 < 95) or (rr > 30) or (sys_bp < 90) or (bt >= 39):
        return "RED"
    # YELLOW (เร่งด่วน)
    if (95 <= o2 <= 96) or (21 <= rr <= 30) or (pr >= 120) or (38 <= bt < 39):
        return "YELLOW"
    return "GREEN"

def make_synth(n=5000, seed=42):
    rng = np.random.default_rng(seed)

    rr = rng.integers(12, 40, size=n)          # RR
    pr = rng.integers(60, 150, size=n)         # PR
    sys_bp = rng.integers(80, 170, size=n)     # Systolic BP
    bt = rng.uniform(36.0, 40.5, size=n).round(1)  # Temperature
    o2 = rng.integers(90, 100, size=n)         # O2Sat

    y = [label_rule(rr[i], pr[i], sys_bp[i], bt[i], o2[i]) for i in range(n)]

    df = pd.DataFrame({
        "rr": rr,
        "pr": pr,
        "sys_bp": sys_bp,
        "bt": bt,
        "o2sat": o2,
        "label": y
    })
    return df

def save_confusion_matrix_csv(cm, labels, path):
    # แปลงเป็นตารางอ่านง่าย (เหมาะมากกับบทที่ 4)
    df_cm = pd.DataFrame(cm, index=[f"true_{l}" for l in labels], columns=[f"pred_{l}" for l in labels])
    df_cm.to_csv(path, index=True)

def main():
    os.makedirs("ai_triage/models", exist_ok=True)
    os.makedirs("ai_triage/reports", exist_ok=True)

    # 1) dataset
    df = make_synth(n=5000, seed=42)
    df.to_csv("ai_triage/reports/synth_dataset.csv", index=False)

    X = df[["rr", "pr", "sys_bp", "bt", "o2sat"]]
    y = df["label"]

    # 2) split (stratify ให้สัดส่วน label ใน train/test ใกล้กัน)
    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.2, random_state=42, stratify=y
    )

    # 3) train
    clf = DecisionTreeClassifier(
        max_depth=4,        # จำกัดความลึกเพื่อกัน overfit + อธิบายง่าย
        random_state=42
    )
    clf.fit(X_train, y_train)

    # 4) evaluate
    pred = clf.predict(X_test)
    acc = accuracy_score(y_test, pred)
    cm = confusion_matrix(y_test, pred, labels=LABELS)
    report = classification_report(y_test, pred, labels=LABELS)

    print("Accuracy:", acc)
    print("Confusion Matrix (labels = GREEN, YELLOW, RED):\n", cm)
    print("\nClassification Report:\n", report)

    # save outputs
    joblib.dump(clf, "ai_triage/models/triage_dt_v1.pkl")

    with open("ai_triage/reports/metrics.txt", "w", encoding="utf-8") as f:
        f.write(f"Accuracy: {acc}\n\n")
        f.write("Confusion Matrix (labels = GREEN, YELLOW, RED):\n")
        f.write(str(cm))
        f.write("\n\nClassification Report:\n")
        f.write(report)

    save_confusion_matrix_csv(cm, LABELS, "ai_triage/reports/confusion_matrix.csv")

    print("\nSaved:")
    print("- ai_triage/models/triage_dt_v1.pkl")
    print("- ai_triage/reports/metrics.txt")
    print("- ai_triage/reports/confusion_matrix.csv")
    print("- ai_triage/reports/synth_dataset.csv")

if __name__ == "__main__":
    main()
