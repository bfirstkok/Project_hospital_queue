from pathlib import Path

import joblib
import pandas as pd

from sklearn.compose import ColumnTransformer
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import (
    accuracy_score,
    balanced_accuracy_score,
    classification_report,
    confusion_matrix,
    f1_score,
)
from sklearn.model_selection import StratifiedKFold, cross_val_predict, train_test_split
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder
from sklearn.impute import SimpleImputer
from sklearn.feature_extraction.text import TfidfVectorizer


BASE_DIR = Path(__file__).resolve().parents[1]
DATA_PATH = BASE_DIR / "data" / "triage_dataset.csv"
LOCAL_CONFIRMED_DATA_PATH = BASE_DIR / "data" / "local_confirmed_triage.csv"
MODEL_PATH = BASE_DIR / "models" / "triage_dt_v1.pkl"
REPORTS_DIR = BASE_DIR / "reports"

MODEL_NAME = "RandomForestClassifier_5Level_RuntimeFeatures_v3"
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
LABELS = ["WHITE", "GREEN", "YELLOW", "PINK", "RED"]
TARGET_EXACT_ACCURACY = 0.75
SEVERITY_RANK = {label: rank for rank, label in enumerate(LABELS, start=1)}
ROUTE_GROUP = {
    "WHITE": "OPD",
    "GREEN": "OPD",
    "YELLOW": "OBSERVATION",
    "PINK": "EMERGENCY",
    "RED": "EMERGENCY",
}
RUNTIME_NUMERIC_FEATURES = [
    "age",
    "nrs_pain",
    "rr",
    "pr",
    "sys_bp",
    "dia_bp",
    "bt",
    "o2sat",
    "lifesaving_intervention",
    "high_risk_condition",
    "altered_mental_status",
    "mental_status",
    "severe_distress",
]
RUNTIME_TEXT_FEATURES = ["chief_complain"]
RUNTIME_CATEGORICAL_FEATURES = ["expected_resources"]


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
    if ktas_level == 1:
        return "RED"
    if ktas_level == 2:
        return "PINK"
    if ktas_level == 3:
        return "YELLOW"
    if ktas_level == 4:
        return "GREEN"
    if ktas_level == 5:
        return "WHITE"
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

    # Dataset dictionary: Mental 1=alert, 2=verbal response,
    # 3=pain response, 4=unresponsive. The production form records AVPU too.
    if "Mental" in raw.columns:
        df["mental_status"] = clean_numeric(raw["Mental"])
        df["altered_mental_status"] = df["mental_status"].where(
            df["mental_status"].isna(),
            (df["mental_status"] > 1).astype(float),
        )

    df["label"] = raw["KTAS_expert"].apply(ktas_to_severity)

    # Do not discard a whole encounter only because one vital sign (especially
    # SpO2) is missing. The preprocessing pipeline imputes numeric gaps. Keep
    # rows with a valid label and enough runtime-observable clinical signal.
    cleaned = df.dropna(subset=["label"]).copy()
    available_runtime_numeric = [
        feature for feature in RUNTIME_NUMERIC_FEATURES if feature in cleaned.columns
    ]
    chief_complaint_present = cleaned.get(
        "chief_complain", pd.Series("", index=cleaned.index)
    ).fillna("").astype(str).str.strip().ne("")
    clinical_signal_present = (
        cleaned[available_runtime_numeric].notna().any(axis=1)
        | chief_complaint_present
    )
    cleaned = cleaned[clinical_signal_present].copy()
    cleaned["label"] = cleaned["label"].astype(str)

    return cleaned


def load_local_confirmed_dataset():
    """Load nurse-confirmed local examples exported by the management command."""
    if not LOCAL_CONFIRMED_DATA_PATH.exists():
        return pd.DataFrame()

    local = pd.read_csv(LOCAL_CONFIRMED_DATA_PATH)
    allowed_columns = set(
        RUNTIME_NUMERIC_FEATURES
        + RUNTIME_TEXT_FEATURES
        + RUNTIME_CATEGORICAL_FEATURES
        + ["label"]
    )
    local = local[[column for column in local.columns if column in allowed_columns]].copy()
    if "label" not in local.columns:
        raise ValueError("local_confirmed_triage.csv must contain a label column")

    for feature in RUNTIME_NUMERIC_FEATURES:
        if feature in local.columns:
            local[feature] = clean_numeric(local[feature])
    if "chief_complain" in local.columns:
        local["chief_complain"] = local["chief_complain"].fillna("").astype(str).str.strip()
    if "expected_resources" in local.columns:
        local["expected_resources"] = local["expected_resources"].astype("string").str.strip()

    local["label"] = local["label"].astype(str).str.upper().str.strip()
    return local[local["label"].isin(LABELS)].copy()


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
                    n_estimators=600,
                    max_depth=None,
                    max_features="sqrt",
                    min_samples_leaf=2,
                    random_state=42,
                    class_weight="balanced_subsample",
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

    reference_df = load_clean_dataset()
    local_df = load_local_confirmed_dataset()
    df = pd.concat([reference_df, local_df], ignore_index=True, sort=False)
    df.to_csv(REPORTS_DIR / "cleaned_dataset.csv", index=False)

    # Train only with fields that production scoring can actually supply.
    # This keeps offline metrics representative of deployed behavior.
    numeric_features = [feature for feature in RUNTIME_NUMERIC_FEATURES if feature in df.columns]
    categorical_features = [
        feature for feature in RUNTIME_CATEGORICAL_FEATURES if feature in df.columns
    ]
    text_features = [feature for feature in RUNTIME_TEXT_FEATURES if feature in df.columns]
    feature_columns = numeric_features + categorical_features + text_features

    X = df[feature_columns]
    y = df["label"]
    class_counts = y.value_counts().reindex(LABELS, fill_value=0)
    sparse_classes = class_counts[class_counts < 50]
    if not sparse_classes.empty:
        print(
            "WARNING: sparse classes (<50 rows): "
            + ", ".join(f"{label}={count}" for label, count in sparse_classes.items())
        )

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

    # Three folds are the maximum defensible stratified split because RED has
    # only three rows. Report this alongside the holdout and fit the deployable
    # artifact on all rows after evaluation.
    cv = StratifiedKFold(n_splits=3, shuffle=True, random_state=42)
    cv_model = build_model(numeric_features, categorical_features, text_features)
    cv_pred = cross_val_predict(cv_model, X, y, cv=cv, n_jobs=-1)
    cv_acc = accuracy_score(y, cv_pred)
    cv_balanced_acc = balanced_accuracy_score(y, cv_pred)
    cv_macro_f1 = f1_score(y, cv_pred, labels=LABELS, average="macro", zero_division=0)
    cv_cm = confusion_matrix(y, cv_pred, labels=LABELS)
    cv_report = classification_report(y, cv_pred, labels=LABELS, zero_division=0)
    cv_route_acc = accuracy_score(
        y.map(ROUTE_GROUP),
        pd.Series(cv_pred, index=y.index).map(ROUTE_GROUP),
    )
    cv_within_one_level = sum(
        abs(SEVERITY_RANK[actual] - SEVERITY_RANK[predicted]) <= 1
        for actual, predicted in zip(y, cv_pred)
    ) / len(y)
    target_achieved = cv_acc >= TARGET_EXACT_ACCURACY

    print("Accuracy:", acc)
    print("Confusion Matrix (labels = WHITE, GREEN, YELLOW, PINK, RED):\n", cm)
    print("\nClassification Report:\n", report)
    print("\n3-fold CV accuracy:", cv_acc)
    print("3-fold CV balanced accuracy:", cv_balanced_acc)
    print("3-fold CV macro F1:", cv_macro_f1)
    print("3-fold CV route accuracy:", cv_route_acc)
    print("3-fold CV within-one-level accuracy:", cv_within_one_level)
    print(f"Exact accuracy target >= {TARGET_EXACT_ACCURACY:.0%} achieved:", target_achieved)
    print("3-fold CV Confusion Matrix:\n", cv_cm)

    model.fit(X, y)
    joblib.dump(model, MODEL_PATH)

    with open(REPORTS_DIR / "metrics.txt", "w", encoding="utf-8") as f:
        f.write("Dataset: ai_triage/data/triage_dataset.csv\n")
        f.write(f"Model name: {MODEL_NAME}\n")
        f.write("Label source: KTAS_expert\n")
        f.write("Severity mapping: KTAS 1=RED, 2=PINK, 3=YELLOW, 4=GREEN, 5=WHITE\n")
        f.write(
            "Data leakage columns excluded: "
            + ", ".join(LEAKAGE_COLUMNS_EXCLUDED)
            + "\n"
        )
        f.write(f"Rows after cleaning: {len(df)}\n")
        f.write(f"Reference rows: {len(reference_df)}\n")
        f.write(f"Local nurse-confirmed rows: {len(local_df)}\n")
        f.write("Class counts: " + ", ".join(f"{label}={class_counts[label]}" for label in LABELS) + "\n")
        if not sparse_classes.empty:
            f.write(
                "Data quality warning: sparse classes (<50 rows): "
                + ", ".join(f"{label}={count}" for label, count in sparse_classes.items())
                + "\n"
            )
        f.write(f"Numeric features: {', '.join(numeric_features)}\n")
        f.write(f"Text features: {', '.join(text_features) or 'None'}\n")
        f.write(f"Categorical features: {', '.join(categorical_features) or 'None'}\n")
        f.write(f"Accuracy: {acc}\n\n")
        f.write("Confusion Matrix (labels = WHITE, GREEN, YELLOW, PINK, RED):\n")
        f.write(str(cm))
        f.write("\n\nClassification Report:\n")
        f.write(report)
        f.write(f"\n\n3-fold CV accuracy: {cv_acc}\n")
        f.write(f"3-fold CV balanced accuracy: {cv_balanced_acc}\n")
        f.write(f"3-fold CV macro F1: {cv_macro_f1}\n\n")
        f.write(f"3-fold CV route accuracy: {cv_route_acc}\n")
        f.write(f"3-fold CV within-one-level accuracy: {cv_within_one_level}\n")
        f.write(
            f"Exact accuracy target >= {TARGET_EXACT_ACCURACY:.0%} achieved: "
            f"{'YES' if target_achieved else 'NO'}\n\n"
        )
        f.write("3-fold CV Confusion Matrix:\n")
        f.write(str(cv_cm))
        f.write("\n\n3-fold CV Classification Report:\n")
        f.write(cv_report)

    save_confusion_matrix_csv(cm, LABELS, REPORTS_DIR / "confusion_matrix.csv")

    print("\nSaved:")
    print(f"- {MODEL_PATH.relative_to(BASE_DIR.parent)}")
    print(f"- {(REPORTS_DIR / 'metrics.txt').relative_to(BASE_DIR.parent)}")
    print(f"- {(REPORTS_DIR / 'confusion_matrix.csv').relative_to(BASE_DIR.parent)}")
    print(f"- {(REPORTS_DIR / 'cleaned_dataset.csv').relative_to(BASE_DIR.parent)}")
    print("\nDecision support only: nurse confirmation remains required.")


if __name__ == "__main__":
    main()
