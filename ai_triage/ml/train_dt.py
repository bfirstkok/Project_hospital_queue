from pathlib import Path

import joblib
import pandas as pd

from sklearn.compose import ColumnTransformer
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import accuracy_score, classification_report, confusion_matrix
from sklearn.model_selection import train_test_split
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder
from sklearn.impute import SimpleImputer
from sklearn.feature_extraction.text import TfidfVectorizer


BASE_DIR = Path(__file__).resolve().parents[1]
DATA_PATH = BASE_DIR / "data" / "triage_dataset.csv"
MODEL_PATH = BASE_DIR / "models" / "triage_dt_v1.pkl"
REPORTS_DIR = BASE_DIR / "reports"

MODEL_NAME = "RandomForestClassifier"
LEAKAGE_COLUMNS_EXCLUDED = [
    "KTAS_expert",
    "KTAS_RN",
    "Error_group",
    "mistriage",
    "Diagnosis in ED",
    "Disposition",
    "Length of stay_min",
    "KTAS duration_min",
]
NUMERIC_SOURCE_TO_FEATURE = {
    "Group": "group",
    "Age": "age",
    "Patients number per hour": "patients_number_per_hour",
    "NRS_pain": "nrs_pain",
    "RR": "rr",
    "HR": "pr",
    "SBP": "sys_bp",
    "DBP": "dia_bp",
    "BT": "bt",
    "Saturation": "o2sat",
}
REQUIRED_FEATURE_COLUMNS = ["rr", "pr", "sys_bp", "bt", "o2sat"]
CATEGORICAL_SOURCE_TO_FEATURE = {
    "Sex": "sex",
    "Arrival mode": "arrival_mode",
    "Injury": "injury",
    "Mental": "mental",
    "Pain": "pain",
}
TEXT_SOURCE_TO_FEATURE = {"Chief_complain": "chief_complain"}
LABELS = ["GREEN", "YELLOW", "RED"]


def clean_numeric(series):
    return pd.to_numeric(
        series.astype(str).str.strip().str.replace(",", ".", regex=False),
        errors="coerce",
    )


def ktas_to_severity(value):
    ktas = pd.to_numeric(str(value).strip().replace(",", "."), errors="coerce")
    if pd.isna(ktas):
        return None

    ktas_level = int(ktas)
    if ktas_level in (1, 2):
        return "RED"
    if ktas_level == 3:
        return "YELLOW"
    if ktas_level in (4, 5):
        return "GREEN"
    return None


def load_clean_dataset():
    raw = pd.read_csv(DATA_PATH, sep=";", encoding="latin1")

    df = pd.DataFrame()
    for source_col, feature_col in NUMERIC_SOURCE_TO_FEATURE.items():
        if source_col in raw.columns:
            df[feature_col] = clean_numeric(raw[source_col])

    for source_col, feature_col in CATEGORICAL_SOURCE_TO_FEATURE.items():
        if source_col in raw.columns:
            df[feature_col] = raw[source_col].astype("string").str.strip()

    for source_col, feature_col in TEXT_SOURCE_TO_FEATURE.items():
        if source_col in raw.columns:
            df[feature_col] = raw[source_col].fillna("").astype(str).str.strip()

    df["label"] = raw["KTAS_expert"].apply(ktas_to_severity)

    cleaned = df.dropna(subset=REQUIRED_FEATURE_COLUMNS + ["label"]).copy()
    cleaned["label"] = cleaned["label"].astype(str)

    return cleaned


def build_model(numeric_features, categorical_features, text_features):
    transformers = []

    if numeric_features:
        numeric_pipeline = Pipeline(
            steps=[
                ("imputer", SimpleImputer(strategy="median")),
            ]
        )
        transformers.append(("numeric", numeric_pipeline, numeric_features))

    if text_features:
        transformers.append(
            (
                "chief_complain",
                TfidfVectorizer(
                    ngram_range=(1, 2),
                    min_df=2,
                    max_features=1000,
                ),
                text_features[0],
            )
        )

    if categorical_features:
        categorical_pipeline = Pipeline(
            steps=[
                ("imputer", SimpleImputer(strategy="most_frequent")),
                ("onehot", OneHotEncoder(handle_unknown="ignore")),
            ]
        )
        transformers.append(("categorical", categorical_pipeline, categorical_features))

    preprocessor = ColumnTransformer(transformers=transformers)

    return Pipeline(
        steps=[
            ("preprocessor", preprocessor),
            (
                "classifier",
                RandomForestClassifier(
                    n_estimators=300,
                    max_depth=None,
                    min_samples_leaf=2,
                    random_state=42,
                    class_weight="balanced",
                ),
            ),
        ]
    )


def save_confusion_matrix_csv(cm, labels, path):
    df_cm = pd.DataFrame(
        cm,
        index=[f"true_{label}" for label in labels],
        columns=[f"pred_{label}" for label in labels],
    )
    df_cm.to_csv(path, index=True)


def main():
    MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)

    df = load_clean_dataset()
    df.to_csv(REPORTS_DIR / "cleaned_dataset.csv", index=False)

    numeric_features = [
        feature for feature in NUMERIC_SOURCE_TO_FEATURE.values() if feature in df.columns
    ]
    categorical_features = [
        feature for feature in CATEGORICAL_SOURCE_TO_FEATURE.values() if feature in df.columns
    ]
    text_features = [
        feature for feature in TEXT_SOURCE_TO_FEATURE.values() if feature in df.columns
    ]
    feature_columns = numeric_features + categorical_features + text_features

    X = df[feature_columns]
    y = df["label"]

    X_train, X_test, y_train, y_test = train_test_split(
        X,
        y,
        test_size=0.2,
        random_state=42,
        stratify=y,
    )

    model = build_model(numeric_features, categorical_features, text_features)
    model.fit(X_train, y_train)

    pred = model.predict(X_test)
    acc = accuracy_score(y_test, pred)
    cm = confusion_matrix(y_test, pred, labels=LABELS)
    report = classification_report(y_test, pred, labels=LABELS, zero_division=0)

    print("Accuracy:", acc)
    print("Confusion Matrix (labels = GREEN, YELLOW, RED):\n", cm)
    print("\nClassification Report:\n", report)

    joblib.dump(model, MODEL_PATH)

    with open(REPORTS_DIR / "metrics.txt", "w", encoding="utf-8") as f:
        f.write("Dataset: ai_triage/data/triage_dataset.csv\n")
        f.write(f"Model name: {MODEL_NAME}\n")
        f.write("Label source: KTAS_expert\n")
        f.write("Severity mapping: KTAS 1-2=RED, KTAS 3=YELLOW, KTAS 4-5=GREEN\n")
        f.write(
            "Data leakage columns excluded: "
            + ", ".join(LEAKAGE_COLUMNS_EXCLUDED)
            + "\n"
        )
        f.write(f"Rows after cleaning: {len(df)}\n")
        f.write(f"Numeric features: {', '.join(numeric_features)}\n")
        f.write(f"Text features: {', '.join(text_features) or 'None'}\n")
        f.write(f"Categorical features: {', '.join(categorical_features) or 'None'}\n")
        f.write(f"Accuracy: {acc}\n\n")
        f.write("Confusion Matrix (labels = GREEN, YELLOW, RED):\n")
        f.write(str(cm))
        f.write("\n\nClassification Report:\n")
        f.write(report)

    save_confusion_matrix_csv(cm, LABELS, REPORTS_DIR / "confusion_matrix.csv")

    print("\nSaved:")
    print(f"- {MODEL_PATH.relative_to(BASE_DIR.parent)}")
    print(f"- {(REPORTS_DIR / 'metrics.txt').relative_to(BASE_DIR.parent)}")
    print(f"- {(REPORTS_DIR / 'confusion_matrix.csv').relative_to(BASE_DIR.parent)}")
    print(f"- {(REPORTS_DIR / 'cleaned_dataset.csv').relative_to(BASE_DIR.parent)}")
    print("\nDecision support only: nurse confirmation remains required.")


if __name__ == "__main__":
    main()
