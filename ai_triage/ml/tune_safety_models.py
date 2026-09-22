from __future__ import annotations

import argparse
import itertools
import json
import time
from collections import Counter
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import RandomForestClassifier
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.impute import SimpleImputer
from sklearn.metrics import (
    accuracy_score,
    balanced_accuracy_score,
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

# Safety-oriented model selection score. RED/PINK together carry 60% of the score.
SCORE_WEIGHTS = {
    "red_recall": 0.35,
    "pink_recall": 0.25,
    "macro_f1": 0.15,
    "balanced_accuracy": 0.10,
    "route_accuracy": 0.10,
    "within_one_level_accuracy": 0.05,
}


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


def severe_undertriage_rate(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    """
    Severe miss = prediction is at least two urgency levels lower than truth.
    Examples: RED -> YELLOW/GREEN/WHITE, PINK -> GREEN/WHITE.
    """
    severe_truth = 0
    severe_misses = 0
    for actual, predicted in zip(y_true, y_pred):
        actual_label = INT_TO_LABEL[int(actual)]
        if actual_label not in {"RED", "PINK"}:
            continue
        severe_truth += 1
        predicted_label = INT_TO_LABEL[int(predicted)]
        if SEVERITY_RANK[actual_label] - SEVERITY_RANK[predicted_label] >= 2:
            severe_misses += 1
    return severe_misses / severe_truth if severe_truth else 0.0


def metrics_for(y_true: np.ndarray, y_pred: np.ndarray) -> dict:
    row = {
        "accuracy": accuracy_score(y_true, y_pred),
        "balanced_accuracy": balanced_accuracy_score(y_true, y_pred),
        "macro_f1": f1_score(
            y_true,
            y_pred,
            labels=list(range(len(LABELS))),
            average="macro",
            zero_division=0,
        ),
        "red_recall": recall_score(
            y_true,
            y_pred,
            labels=[LABEL_TO_INT["RED"]],
            average="macro",
            zero_division=0,
        ),
        "pink_recall": recall_score(
            y_true,
            y_pred,
            labels=[LABEL_TO_INT["PINK"]],
            average="macro",
            zero_division=0,
        ),
        "white_recall": recall_score(
            y_true,
            y_pred,
            labels=[LABEL_TO_INT["WHITE"]],
            average="macro",
            zero_division=0,
        ),
        "route_accuracy": route_accuracy(y_true, y_pred),
        "within_one_level_accuracy": within_one_level_accuracy(y_true, y_pred),
        "severe_undertriage_rate": severe_undertriage_rate(y_true, y_pred),
    }
    row["critical_weighted_score"] = sum(
        row[name] * weight for name, weight in SCORE_WEIGHTS.items()
    )
    return row


def make_sample_weight(y: np.ndarray, profile: str | None):
    if not profile:
        return None

    base = compute_sample_weight(class_weight="balanced", y=y)
    if profile == "critical_mild":
        multipliers = {"RED": 1.50, "PINK": 1.20}
    elif profile == "critical_strong":
        multipliers = {"RED": 2.00, "PINK": 1.40}
    else:
        raise ValueError(f"Unknown sample-weight profile: {profile}")

    adjusted = base.astype(float)
    for index, value in enumerate(y):
        label = INT_TO_LABEL[int(value)]
        adjusted[index] *= multipliers.get(label, 1.0)
    return adjusted


def build_pipeline(
    estimator,
    numeric_features,
    categorical_features,
    text_features,
):
    return Pipeline(
        [
            (
                "preprocessor",
                build_preprocessor(
                    numeric_features,
                    categorical_features,
                    text_features,
                ),
            ),
            ("classifier", estimator),
        ]
    )


def fit_predict(
    candidate,
    X_train,
    y_train,
    X_test,
    numeric_features,
    categorical_features,
    text_features,
):
    pipeline = build_pipeline(
        candidate["factory"](),
        numeric_features,
        categorical_features,
        text_features,
    )
    sample_weight = make_sample_weight(y_train, candidate.get("weight_profile"))
    fit_kwargs = {}
    if sample_weight is not None:
        fit_kwargs["classifier__sample_weight"] = sample_weight
    pipeline.fit(X_train, y_train, **fit_kwargs)
    return np.asarray(pipeline.predict(X_test)).reshape(-1).astype(int)


def random_forest_candidates():
    specs = [
        (
            "rf_current",
            None,
            dict(
                n_estimators=600,
                max_depth=None,
                max_features="sqrt",
                min_samples_leaf=2,
                class_weight="balanced_subsample",
            ),
        ),
        (
            "rf_leaf1",
            None,
            dict(
                n_estimators=900,
                max_depth=None,
                max_features="sqrt",
                min_samples_leaf=1,
                class_weight="balanced_subsample",
            ),
        ),
        (
            "rf_depth16_leaf1",
            None,
            dict(
                n_estimators=900,
                max_depth=16,
                max_features="sqrt",
                min_samples_leaf=1,
                class_weight="balanced_subsample",
            ),
        ),
        (
            "rf_depth20_leaf2",
            None,
            dict(
                n_estimators=900,
                max_depth=20,
                max_features="sqrt",
                min_samples_leaf=2,
                class_weight="balanced_subsample",
            ),
        ),
        (
            "rf_features07_leaf2",
            None,
            dict(
                n_estimators=700,
                max_depth=None,
                max_features=0.7,
                min_samples_leaf=2,
                class_weight="balanced_subsample",
            ),
        ),
        (
            "rf_features07_leaf1",
            None,
            dict(
                n_estimators=700,
                max_depth=None,
                max_features=0.7,
                min_samples_leaf=1,
                class_weight="balanced_subsample",
            ),
        ),
        (
            "rf_critical_mild_leaf2",
            "critical_mild",
            dict(
                n_estimators=800,
                max_depth=None,
                max_features="sqrt",
                min_samples_leaf=2,
                class_weight=None,
            ),
        ),
        (
            "rf_critical_strong_leaf2",
            "critical_strong",
            dict(
                n_estimators=800,
                max_depth=None,
                max_features="sqrt",
                min_samples_leaf=2,
                class_weight=None,
            ),
        ),
        (
            "rf_critical_mild_leaf1",
            "critical_mild",
            dict(
                n_estimators=800,
                max_depth=None,
                max_features="sqrt",
                min_samples_leaf=1,
                class_weight=None,
            ),
        ),
        (
            "rf_critical_mild_depth16",
            "critical_mild",
            dict(
                n_estimators=800,
                max_depth=16,
                max_features="sqrt",
                min_samples_leaf=1,
                class_weight=None,
            ),
        ),
    ]

    candidates = []
    for name, weight_profile, params in specs:
        candidates.append(
            {
                "name": name,
                "family": "Random Forest",
                "weight_profile": weight_profile,
                "params": params,
                "factory": lambda params=params: RandomForestClassifier(
                    **params,
                    random_state=42,
                    n_jobs=2,
                ),
            }
        )
    return candidates


def catboost_candidates():
    from catboost import CatBoostClassifier

    specs = [
        (
            "cat_base",
            None,
            dict(
                iterations=500,
                depth=6,
                learning_rate=0.05,
                l2_leaf_reg=3,
                auto_class_weights="Balanced",
            ),
        ),
        (
            "cat_depth5",
            None,
            dict(
                iterations=700,
                depth=5,
                learning_rate=0.05,
                l2_leaf_reg=3,
                auto_class_weights="Balanced",
            ),
        ),
        (
            "cat_depth7",
            None,
            dict(
                iterations=700,
                depth=7,
                learning_rate=0.04,
                l2_leaf_reg=5,
                auto_class_weights="Balanced",
            ),
        ),
        (
            "cat_depth4_fast",
            None,
            dict(
                iterations=600,
                depth=4,
                learning_rate=0.07,
                l2_leaf_reg=3,
                auto_class_weights="Balanced",
            ),
        ),
        (
            "cat_slow",
            None,
            dict(
                iterations=900,
                depth=6,
                learning_rate=0.03,
                l2_leaf_reg=8,
                auto_class_weights="Balanced",
            ),
        ),
        (
            "cat_reg10",
            None,
            dict(
                iterations=700,
                depth=6,
                learning_rate=0.05,
                l2_leaf_reg=10,
                auto_class_weights="Balanced",
            ),
        ),
        (
            "cat_critical_mild_d6",
            "critical_mild",
            dict(
                iterations=700,
                depth=6,
                learning_rate=0.05,
                l2_leaf_reg=5,
                auto_class_weights=None,
            ),
        ),
        (
            "cat_critical_strong_d6",
            "critical_strong",
            dict(
                iterations=700,
                depth=6,
                learning_rate=0.05,
                l2_leaf_reg=5,
                auto_class_weights=None,
            ),
        ),
        (
            "cat_critical_mild_d5",
            "critical_mild",
            dict(
                iterations=700,
                depth=5,
                learning_rate=0.05,
                l2_leaf_reg=5,
                auto_class_weights=None,
            ),
        ),
        (
            "cat_critical_mild_d7",
            "critical_mild",
            dict(
                iterations=700,
                depth=7,
                learning_rate=0.04,
                l2_leaf_reg=8,
                auto_class_weights=None,
            ),
        ),
    ]

    candidates = []
    for name, weight_profile, params in specs:
        candidates.append(
            {
                "name": name,
                "family": "CatBoost",
                "weight_profile": weight_profile,
                "params": params,
                "factory": lambda params=params: CatBoostClassifier(
                    **params,
                    loss_function="MultiClass",
                    random_seed=42,
                    thread_count=2,
                    verbose=False,
                    allow_writing_files=False,
                ),
            }
        )
    return candidates


def inner_score_candidate(
    candidate,
    X_train,
    y_train,
    numeric_features,
    categorical_features,
    text_features,
    seed,
):
    cv = StratifiedKFold(n_splits=3, shuffle=True, random_state=seed)
    predictions = np.empty_like(y_train)

    for inner_train_idx, inner_test_idx in cv.split(X_train, y_train):
        predictions[inner_test_idx] = fit_predict(
            candidate,
            X_train.iloc[inner_train_idx],
            y_train[inner_train_idx],
            X_train.iloc[inner_test_idx],
            numeric_features,
            categorical_features,
            text_features,
        )

    return metrics_for(y_train, predictions)


def select_candidate(
    candidates,
    X_train,
    y_train,
    numeric_features,
    categorical_features,
    text_features,
    seed,
):
    rows = []
    for candidate in candidates:
        started = time.perf_counter()
        metrics = inner_score_candidate(
            candidate,
            X_train,
            y_train,
            numeric_features,
            categorical_features,
            text_features,
            seed,
        )
        rows.append(
            {
                "candidate": candidate["name"],
                "family": candidate["family"],
                "weight_profile": candidate.get("weight_profile") or "default",
                "fit_seconds": time.perf_counter() - started,
                **metrics,
            }
        )

    ranking = sorted(
        rows,
        key=lambda row: (
            row["critical_weighted_score"],
            row["red_recall"],
            row["pink_recall"],
            row["macro_f1"],
        ),
        reverse=True,
    )
    best_name = ranking[0]["candidate"]
    best_candidate = next(
        candidate for candidate in candidates if candidate["name"] == best_name
    )
    return best_candidate, rows


def nested_cv_family(
    family_name,
    candidates,
    X,
    y,
    numeric_features,
    categorical_features,
    text_features,
):
    outer_cv = StratifiedKFold(n_splits=3, shuffle=True, random_state=20260922)
    outer_predictions = np.empty_like(y)
    selections = []
    inner_rows = []

    for outer_fold, (train_idx, test_idx) in enumerate(
        outer_cv.split(X, y),
        start=1,
    ):
        X_train = X.iloc[train_idx]
        X_test = X.iloc[test_idx]
        y_train = y[train_idx]

        best_candidate, fold_inner_rows = select_candidate(
            candidates,
            X_train,
            y_train,
            numeric_features,
            categorical_features,
            text_features,
            seed=7000 + outer_fold,
        )
        for row in fold_inner_rows:
            row["outer_fold"] = outer_fold
            inner_rows.append(row)

        started = time.perf_counter()
        outer_pred = fit_predict(
            best_candidate,
            X_train,
            y_train,
            X_test,
            numeric_features,
            categorical_features,
            text_features,
        )
        elapsed = time.perf_counter() - started
        outer_predictions[test_idx] = outer_pred

        selections.append(
            {
                "family": family_name,
                "outer_fold": outer_fold,
                "candidate": best_candidate["name"],
                "weight_profile": best_candidate.get("weight_profile") or "default",
                "params": best_candidate["params"],
                "outer_fit_seconds": elapsed,
            }
        )

    overall = metrics_for(y, outer_predictions)
    overall["family"] = family_name
    overall["selected_candidates"] = dict(
        Counter(row["candidate"] for row in selections)
    )
    return overall, selections, inner_rows, outer_predictions


def evaluate_baseline_rf(
    X,
    y,
    numeric_features,
    categorical_features,
    text_features,
):
    current = random_forest_candidates()[0]
    outer_cv = StratifiedKFold(n_splits=3, shuffle=True, random_state=20260922)
    predictions = np.empty_like(y)

    for train_idx, test_idx in outer_cv.split(X, y):
        predictions[test_idx] = fit_predict(
            current,
            X.iloc[train_idx],
            y[train_idx],
            X.iloc[test_idx],
            numeric_features,
            categorical_features,
            text_features,
        )

    result = metrics_for(y, predictions)
    result["family"] = "Random Forest current (untuned)"
    result["selected_candidates"] = {"rf_current": 3}
    return result, predictions


def fmt_pct(value):
    return f"{100 * value:.2f}%"


def write_report(path: Path, results: list[dict], selections: list[dict]):
    ranked = sorted(
        results,
        key=lambda row: row["critical_weighted_score"],
        reverse=True,
    )
    lines = [
        "# Safety-weighted triage tuning",
        "",
        "Nested cross-validation is used so hyperparameter selection happens only "
        "inside each outer training fold. The reported outer-fold metrics are "
        "therefore less optimistic than choosing and reporting on the same folds.",
        "",
        "## Selection score",
        "",
        "Critical Weighted Score = "
        "35% RED recall + 25% PINK recall + 15% Macro F1 + "
        "10% Balanced Accuracy + 10% Route Accuracy + 5% Within ±1 level.",
        "",
        "| Model | Critical score | Accuracy | Balanced Acc. | Macro F1 | RED Recall | PINK Recall | WHITE Recall | Route Acc. | ±1 level | Severe undertriage |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in ranked:
        lines.append(
            "| {family} | {score} | {accuracy} | {balanced} | {macro} | "
            "{red} | {pink} | {white} | {route} | {within} | {under} |".format(
                family=row["family"],
                score=fmt_pct(row["critical_weighted_score"]),
                accuracy=fmt_pct(row["accuracy"]),
                balanced=fmt_pct(row["balanced_accuracy"]),
                macro=fmt_pct(row["macro_f1"]),
                red=fmt_pct(row["red_recall"]),
                pink=fmt_pct(row["pink_recall"]),
                white=fmt_pct(row["white_recall"]),
                route=fmt_pct(row["route_accuracy"]),
                within=fmt_pct(row["within_one_level_accuracy"]),
                under=fmt_pct(row["severe_undertriage_rate"]),
            )
        )

    lines.extend(["", "## Hyperparameters selected in each outer fold", ""])
    for family in ("Random Forest tuned", "CatBoost tuned"):
        lines.append(f"### {family}")
        for row in selections:
            if row["family"] != family:
                continue
            lines.append(
                f"- Fold {row['outer_fold']}: {row['candidate']} "
                f"(weights={row['weight_profile']}) — {row['params']}"
            )
        lines.append("")

    path.write_text("\n".join(lines), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output-dir",
        default="ai_triage/reports/safety_tuning",
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
    y = df["label"].astype(str).map(LABEL_TO_INT).to_numpy(dtype=int)

    print(f"Rows: {len(df)}")
    print(
        "Class counts:",
        df["label"].value_counts().reindex(LABELS, fill_value=0).to_dict(),
    )
    print("Safety score weights:", SCORE_WEIGHTS)

    baseline_result, _ = evaluate_baseline_rf(
        X,
        y,
        numeric_features,
        categorical_features,
        text_features,
    )
    print("\nCurrent RF outer-CV:", baseline_result)

    rf_result, rf_selections, rf_inner, _ = nested_cv_family(
        "Random Forest tuned",
        random_forest_candidates(),
        X,
        y,
        numeric_features,
        categorical_features,
        text_features,
    )
    print("\nTuned RF outer-CV:", rf_result)
    print("RF selections:", rf_selections)

    cat_result, cat_selections, cat_inner, _ = nested_cv_family(
        "CatBoost tuned",
        catboost_candidates(),
        X,
        y,
        numeric_features,
        categorical_features,
        text_features,
    )
    print("\nTuned CatBoost outer-CV:", cat_result)
    print("CatBoost selections:", cat_selections)

    results = [baseline_result, rf_result, cat_result]
    selections = rf_selections + cat_selections
    inner_rows = rf_inner + cat_inner

    pd.DataFrame(
        [
            {
                key: value
                for key, value in row.items()
                if key != "selected_candidates"
            }
            for row in results
        ]
    ).to_csv(output_dir / "outer_cv_summary.csv", index=False)
    pd.DataFrame(inner_rows).to_csv(
        output_dir / "inner_search_metrics.csv",
        index=False,
    )
    (output_dir / "selections.json").write_text(
        json.dumps(selections, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (output_dir / "results.json").write_text(
        json.dumps(
            {
                "score_weights": SCORE_WEIGHTS,
                "results": results,
                "selections": selections,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    write_report(output_dir / "README.md", results, selections)

    print("\n=== Final safety-weighted comparison ===")
    print((output_dir / "README.md").read_text(encoding="utf-8"))


if __name__ == "__main__":
    main()
