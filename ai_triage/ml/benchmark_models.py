from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import numpy as np
import pandas as pd
import sklearn
from sklearn.base import clone
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import RandomForestClassifier
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (
    accuracy_score,
    balanced_accuracy_score,
    confusion_matrix,
    f1_score,
    recall_score,
)
from sklearn.model_selection import StratifiedKFold
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder
from sklearn.utils.class_weight import compute_sample_weight

from ai_triage.ml.train_dt import (
    LABELS,
    ROUTE_GROUP,
    RUNTIME_CATEGORICAL_FEATURES,
    RUNTIME_NUMERIC_FEATURES,
    RUNTIME_TEXT_FEATURES,
    SEVERITY_RANK,
    load_clean_dataset,
    load_local_confirmed_dataset,
)


LABEL_TO_INT = {label: index for index, label in enumerate(LABELS)}
INT_TO_LABEL = {index: label for label, index in LABEL_TO_INT.items()}


def build_preprocessor(
    numeric_features: list[str],
    categorical_features: list[str],
    text_features: list[str],
) -> ColumnTransformer:
    transformers = []

    if numeric_features:
        transformers.append(
            (
                "numeric",
                Pipeline([("imputer", SimpleImputer(strategy="median"))]),
                numeric_features,
            )
        )

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
        transformers.append(
            (
                "categorical",
                Pipeline(
                    [
                        ("imputer", SimpleImputer(strategy="most_frequent")),
                        ("onehot", OneHotEncoder(handle_unknown="ignore")),
                    ]
                ),
                categorical_features,
            )
        )

    return ColumnTransformer(transformers=transformers)


def model_factories():
    factories = {
        "Random Forest (current)": lambda: RandomForestClassifier(
            n_estimators=600,
            max_depth=None,
            max_features="sqrt",
            min_samples_leaf=2,
            random_state=42,
            class_weight="balanced_subsample",
            n_jobs=2,
        ),
        "Logistic Regression": lambda: LogisticRegression(
            max_iter=5000,
            solver="lbfgs",
            class_weight="balanced",
            random_state=42,
        ),
    }

    try:
        from xgboost import XGBClassifier

        factories["XGBoost"] = lambda: XGBClassifier(
            objective="multi:softprob",
            num_class=len(LABELS),
            n_estimators=500,
            max_depth=5,
            learning_rate=0.05,
            subsample=0.90,
            colsample_bytree=0.90,
            reg_lambda=1.0,
            random_state=42,
            n_jobs=2,
            tree_method="hist",
            eval_metric="mlogloss",
        )
    except Exception:
        pass

    try:
        from catboost import CatBoostClassifier

        factories["CatBoost"] = lambda: CatBoostClassifier(
            loss_function="MultiClass",
            iterations=500,
            depth=6,
            learning_rate=0.05,
            auto_class_weights="Balanced",
            random_seed=42,
            thread_count=2,
            verbose=False,
            allow_writing_files=False,
        )
    except Exception:
        pass

    try:
        from lightgbm import LGBMClassifier

        factories["LightGBM"] = lambda: LGBMClassifier(
            objective="multiclass",
            num_class=len(LABELS),
            n_estimators=500,
            learning_rate=0.05,
            num_leaves=31,
            max_depth=-1,
            class_weight="balanced",
            random_state=42,
            n_jobs=2,
            verbosity=-1,
        )
    except Exception:
        pass

    return factories


def route_accuracy(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    true_routes = [ROUTE_GROUP[INT_TO_LABEL[int(value)]] for value in y_true]
    pred_routes = [ROUTE_GROUP[INT_TO_LABEL[int(value)]] for value in y_pred]
    return accuracy_score(true_routes, pred_routes)


def within_one_level_accuracy(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    correct = 0
    for actual, predicted in zip(y_true, y_pred):
        actual_label = INT_TO_LABEL[int(actual)]
        predicted_label = INT_TO_LABEL[int(predicted)]
        if abs(SEVERITY_RANK[actual_label] - SEVERITY_RANK[predicted_label]) <= 1:
            correct += 1
    return correct / len(y_true)


def evaluate_model(
    name: str,
    classifier_factory,
    X: pd.DataFrame,
    y: np.ndarray,
    numeric_features: list[str],
    categorical_features: list[str],
    text_features: list[str],
):
    cv = StratifiedKFold(n_splits=3, shuffle=True, random_state=42)
    predictions = np.empty_like(y)
    fold_rows = []
    total_fit_seconds = 0.0

    for fold_number, (train_index, test_index) in enumerate(cv.split(X, y), start=1):
        X_train = X.iloc[train_index]
        X_test = X.iloc[test_index]
        y_train = y[train_index]
        y_test = y[test_index]

        pipeline = Pipeline(
            [
                (
                    "preprocessor",
                    build_preprocessor(
                        numeric_features,
                        categorical_features,
                        text_features,
                    ),
                ),
                ("classifier", classifier_factory()),
            ]
        )

        fit_kwargs = {}
        if name == "XGBoost":
            fit_kwargs["classifier__sample_weight"] = compute_sample_weight(
                class_weight="balanced",
                y=y_train,
            )

        started = time.perf_counter()
        pipeline.fit(X_train, y_train, **fit_kwargs)
        fit_seconds = time.perf_counter() - started
        total_fit_seconds += fit_seconds

        fold_pred = np.asarray(pipeline.predict(X_test)).reshape(-1).astype(int)
        predictions[test_index] = fold_pred

        fold_rows.append(
            {
                "model": name,
                "fold": fold_number,
                "accuracy": accuracy_score(y_test, fold_pred),
                "balanced_accuracy": balanced_accuracy_score(y_test, fold_pred),
                "macro_f1": f1_score(
                    y_test,
                    fold_pred,
                    labels=list(range(len(LABELS))),
                    average="macro",
                    zero_division=0,
                ),
                "red_recall": recall_score(
                    y_test,
                    fold_pred,
                    labels=[LABEL_TO_INT["RED"]],
                    average="macro",
                    zero_division=0,
                ),
                "pink_recall": recall_score(
                    y_test,
                    fold_pred,
                    labels=[LABEL_TO_INT["PINK"]],
                    average="macro",
                    zero_division=0,
                ),
                "route_accuracy": route_accuracy(y_test, fold_pred),
                "within_one_level_accuracy": within_one_level_accuracy(
                    y_test, fold_pred
                ),
                "fit_seconds": fit_seconds,
            }
        )

    result = {
        "model": name,
        "accuracy": accuracy_score(y, predictions),
        "balanced_accuracy": balanced_accuracy_score(y, predictions),
        "macro_f1": f1_score(
            y,
            predictions,
            labels=list(range(len(LABELS))),
            average="macro",
            zero_division=0,
        ),
        "red_recall": recall_score(
            y,
            predictions,
            labels=[LABEL_TO_INT["RED"]],
            average="macro",
            zero_division=0,
        ),
        "pink_recall": recall_score(
            y,
            predictions,
            labels=[LABEL_TO_INT["PINK"]],
            average="macro",
            zero_division=0,
        ),
        "white_recall": recall_score(
            y,
            predictions,
            labels=[LABEL_TO_INT["WHITE"]],
            average="macro",
            zero_division=0,
        ),
        "route_accuracy": route_accuracy(y, predictions),
        "within_one_level_accuracy": within_one_level_accuracy(y, predictions),
        "fit_seconds": total_fit_seconds,
        "confusion_matrix": confusion_matrix(
            y,
            predictions,
            labels=list(range(len(LABELS))),
        ).tolist(),
    }
    return result, fold_rows


def package_versions():
    versions = {"scikit-learn": sklearn.__version__}
    for package_name, import_name in (
        ("xgboost", "xgboost"),
        ("catboost", "catboost"),
        ("lightgbm", "lightgbm"),
    ):
        try:
            module = __import__(import_name)
            versions[package_name] = getattr(module, "__version__", "unknown")
        except Exception:
            versions[package_name] = "not installed"
    return versions


def pct(value: float) -> str:
    return f"{value * 100:.2f}%"


def write_markdown(
    output_path: Path,
    results: list[dict],
    row_count: int,
    class_counts: pd.Series,
    numeric_features: list[str],
    categorical_features: list[str],
    text_features: list[str],
):
    ranking = sorted(
        results,
        key=lambda row: (
            row["macro_f1"],
            row["balanced_accuracy"],
            row["red_recall"],
        ),
        reverse=True,
    )

    lines = [
        "# Triage model benchmark",
        "",
        "All models use the same cleaned dataset, runtime-observable features, "
        "TF-IDF preprocessing, and the same 3-fold StratifiedKFold splits "
        "(shuffle=True, random_state=42).",
        "",
        f"- Rows: {row_count}",
        "- Labels: " + ", ".join(LABELS),
        "- Class counts: "
        + ", ".join(f"{label}={int(class_counts[label])}" for label in LABELS),
        "- Numeric features: " + ", ".join(numeric_features),
        "- Text features: " + (", ".join(text_features) or "None"),
        "- Categorical features: " + (", ".join(categorical_features) or "None"),
        "",
        "## Summary",
        "",
        "| Model | Accuracy | Balanced Acc. | Macro F1 | RED Recall | PINK Recall | WHITE Recall | Route Acc. | Within ±1 level | Fit time |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]

    for row in ranking:
        lines.append(
            "| {model} | {accuracy} | {balanced_accuracy} | {macro_f1} | "
            "{red_recall} | {pink_recall} | {white_recall} | "
            "{route_accuracy} | {within_one_level_accuracy} | {fit_seconds:.1f}s |".format(
                model=row["model"],
                accuracy=pct(row["accuracy"]),
                balanced_accuracy=pct(row["balanced_accuracy"]),
                macro_f1=pct(row["macro_f1"]),
                red_recall=pct(row["red_recall"]),
                pink_recall=pct(row["pink_recall"]),
                white_recall=pct(row["white_recall"]),
                route_accuracy=pct(row["route_accuracy"]),
                within_one_level_accuracy=pct(row["within_one_level_accuracy"]),
                fit_seconds=row["fit_seconds"],
            )
        )

    lines.extend(
        [
            "",
            "## Package versions",
            "",
        ]
    )
    for package, version in package_versions().items():
        lines.append(f"- {package}: {version}")

    lines.extend(
        [
            "",
            "## Interpretation note",
            "",
            "This benchmark is for model comparison only. It does not replace "
            "the rule-based safety guardrails or nurse confirmation in the application.",
            "The dataset is imbalanced, especially for RED, so RED/PINK recall, "
            "balanced accuracy, and macro F1 should be considered alongside overall accuracy.",
            "",
        ]
    )

    output_path.write_text("\n".join(lines), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output-dir",
        default="ai_triage/reports/model_benchmark",
    )
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    reference_df = load_clean_dataset()
    local_df = load_local_confirmed_dataset()
    df = pd.concat([reference_df, local_df], ignore_index=True, sort=False)

    numeric_features = [
        feature for feature in RUNTIME_NUMERIC_FEATURES if feature in df.columns
    ]
    categorical_features = [
        feature for feature in RUNTIME_CATEGORICAL_FEATURES if feature in df.columns
    ]
    text_features = [
        feature for feature in RUNTIME_TEXT_FEATURES if feature in df.columns
    ]
    feature_columns = numeric_features + categorical_features + text_features

    X = df[feature_columns].copy()
    y_labels = df["label"].astype(str)
    y = y_labels.map(LABEL_TO_INT).to_numpy(dtype=int)
    class_counts = y_labels.value_counts().reindex(LABELS, fill_value=0)

    all_results = []
    all_folds = []

    print(f"Benchmark rows: {len(df)}")
    print("Class counts:", class_counts.to_dict())
    print("Models:", ", ".join(model_factories().keys()))

    for name, factory in model_factories().items():
        print(f"\n=== {name} ===", flush=True)
        result, fold_rows = evaluate_model(
            name,
            factory,
            X,
            y,
            numeric_features,
            categorical_features,
            text_features,
        )
        all_results.append(result)
        all_folds.extend(fold_rows)
        print(
            f"Accuracy={result['accuracy']:.4f} "
            f"BalancedAcc={result['balanced_accuracy']:.4f} "
            f"MacroF1={result['macro_f1']:.4f} "
            f"REDRecall={result['red_recall']:.4f} "
            f"PINKRecall={result['pink_recall']:.4f} "
            f"RouteAcc={result['route_accuracy']:.4f}"
        )

    summary_df = pd.DataFrame(
        [
            {key: value for key, value in row.items() if key != "confusion_matrix"}
            for row in all_results
        ]
    ).sort_values(
        ["macro_f1", "balanced_accuracy", "red_recall"],
        ascending=False,
    )
    summary_df.to_csv(output_dir / "summary.csv", index=False)
    pd.DataFrame(all_folds).to_csv(output_dir / "fold_metrics.csv", index=False)

    (output_dir / "results.json").write_text(
        json.dumps(
            {
                "rows": len(df),
                "class_counts": {
                    label: int(class_counts[label]) for label in LABELS
                },
                "features": {
                    "numeric": numeric_features,
                    "categorical": categorical_features,
                    "text": text_features,
                },
                "package_versions": package_versions(),
                "results": all_results,
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    write_markdown(
        output_dir / "README.md",
        all_results,
        len(df),
        class_counts,
        numeric_features,
        categorical_features,
        text_features,
    )

    print("\n=== Ranked summary (macro F1 first) ===")
    print(
        summary_df[
            [
                "model",
                "accuracy",
                "balanced_accuracy",
                "macro_f1",
                "red_recall",
                "pink_recall",
                "white_recall",
                "route_accuracy",
                "within_one_level_accuracy",
                "fit_seconds",
            ]
        ].to_string(index=False)
    )
    print(f"\nSaved benchmark files to {output_dir}")


if __name__ == "__main__":
    main()
