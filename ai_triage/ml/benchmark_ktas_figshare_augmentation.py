from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.metrics import (
    accuracy_score,
    balanced_accuracy_score,
    f1_score,
    recall_score,
)
from sklearn.model_selection import StratifiedKFold

from ai_triage.ml.train_dt import (
    LABELS,
    ROUTE_GROUP,
    RUNTIME_CATEGORICAL_FEATURES,
    RUNTIME_NUMERIC_FEATURES,
    RUNTIME_TEXT_FEATURES,
    SEVERITY_RANK,
    build_model,
    load_clean_dataset,
    load_local_confirmed_dataset,
)


def route_accuracy(y_true, y_pred) -> float:
    return accuracy_score(
        [ROUTE_GROUP[str(value)] for value in y_true],
        [ROUTE_GROUP[str(value)] for value in y_pred],
    )


def within_one_level(y_true, y_pred) -> float:
    ok = 0
    for actual, predicted in zip(y_true, y_pred):
        if abs(SEVERITY_RANK[str(actual)] - SEVERITY_RANK[str(predicted)]) <= 1:
            ok += 1
    return ok / len(y_true)


def severe_undertriage(y_true, y_pred) -> float:
    total = 0
    misses = 0
    for actual, predicted in zip(y_true, y_pred):
        actual = str(actual)
        predicted = str(predicted)
        if actual not in {"RED", "PINK"}:
            continue
        total += 1
        if SEVERITY_RANK[actual] - SEVERITY_RANK[predicted] >= 2:
            misses += 1
    return misses / total if total else 0.0


def metrics(y_true, y_pred) -> dict:
    return {
        "accuracy": accuracy_score(y_true, y_pred),
        "balanced_accuracy": balanced_accuracy_score(y_true, y_pred),
        "macro_f1": f1_score(
            y_true,
            y_pred,
            labels=LABELS,
            average="macro",
            zero_division=0,
        ),
        "red_recall": recall_score(
            y_true,
            y_pred,
            labels=["RED"],
            average="macro",
            zero_division=0,
        ),
        "pink_recall": recall_score(
            y_true,
            y_pred,
            labels=["PINK"],
            average="macro",
            zero_division=0,
        ),
        "white_recall": recall_score(
            y_true,
            y_pred,
            labels=["WHITE"],
            average="macro",
            zero_division=0,
        ),
        "route_accuracy": route_accuracy(y_true, y_pred),
        "within_one_level_accuracy": within_one_level(y_true, y_pred),
        "severe_undertriage_rate": severe_undertriage(y_true, y_pred),
    }


def sample_external(
    external: pd.DataFrame,
    *,
    max_per_class: int,
    random_state: int,
) -> pd.DataFrame:
    parts = []
    for label in LABELS:
        group = external[external["label"] == label]
        if group.empty:
            continue
        if max_per_class > 0 and len(group) > max_per_class:
            group = group.sample(n=max_per_class, random_state=random_state)
        parts.append(group)
    if not parts:
        raise ValueError("No valid KTAS external rows available")
    return pd.concat(parts, ignore_index=True, sort=False)


def evaluate_weight(
    project: pd.DataFrame,
    external: pd.DataFrame,
    *,
    features: list[str],
    numeric_features: list[str],
    categorical_features: list[str],
    text_features: list[str],
    external_weight: float,
) -> dict:
    X_project = project[features].copy()
    y_project = project["label"].astype(str).to_numpy()

    cv = StratifiedKFold(n_splits=3, shuffle=True, random_state=42)
    baseline_predictions = np.empty(len(project), dtype=object)
    augmented_predictions = np.empty(len(project), dtype=object)

    for train_idx, test_idx in cv.split(X_project, y_project):
        X_train = X_project.iloc[train_idx].copy()
        X_test = X_project.iloc[test_idx].copy()
        y_train = y_project[train_idx]

        baseline = build_model(
            numeric_features,
            categorical_features,
            text_features,
        )
        baseline.fit(X_train, y_train)
        baseline_predictions[test_idx] = baseline.predict(X_test)

        X_ext = external[features].copy()
        y_ext = external["label"].astype(str).to_numpy()

        X_aug = pd.concat([X_train, X_ext], ignore_index=True, sort=False)
        y_aug = np.concatenate([y_train, y_ext])
        sample_weight = np.concatenate(
            [
                np.ones(len(y_train), dtype=float),
                np.full(len(y_ext), external_weight, dtype=float),
            ]
        )

        augmented = build_model(
            numeric_features,
            categorical_features,
            text_features,
        )
        augmented.fit(
            X_aug,
            y_aug,
            classifier__sample_weight=sample_weight,
        )
        augmented_predictions[test_idx] = augmented.predict(X_test)

    baseline_metrics = metrics(y_project, baseline_predictions)
    augmented_metrics = metrics(y_project, augmented_predictions)

    return {
        "external_weight": external_weight,
        "baseline": baseline_metrics,
        "augmented": augmented_metrics,
        "delta": {
            name: augmented_metrics[name] - baseline_metrics[name]
            for name in baseline_metrics
        },
    }


def pct(value: float) -> str:
    return f"{value * 100:.2f}%"


def main():
    parser = argparse.ArgumentParser(
        description=(
            "Evaluate low-weight augmentation using the larger public KTAS "
            "Figshare cohort. Validation remains project-only."
        )
    )
    parser.add_argument("--ktas-csv", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument(
        "--max-external-per-class",
        type=int,
        default=2000,
    )
    parser.add_argument(
        "--weights",
        default="0.005,0.01,0.025,0.05,0.10",
    )
    args = parser.parse_args()

    reference = load_clean_dataset()
    local = load_local_confirmed_dataset()
    project = pd.concat([reference, local], ignore_index=True, sort=False)

    external = pd.read_csv(args.ktas_csv, low_memory=False)
    external["label"] = external["label"].astype(str).str.upper().str.strip()
    external = external[external["label"].isin(LABELS)].copy()
    full_external_rows = len(external)
    external = sample_external(
        external,
        max_per_class=args.max_external_per_class,
        random_state=42,
    )

    numeric_features = [
        feature for feature in RUNTIME_NUMERIC_FEATURES if feature in project.columns
    ]
    categorical_features = [
        feature for feature in RUNTIME_CATEGORICAL_FEATURES if feature in project.columns
    ]
    text_features = [
        feature for feature in RUNTIME_TEXT_FEATURES if feature in project.columns
    ]
    features = numeric_features + categorical_features + text_features

    for feature in features:
        if feature not in external.columns:
            external[feature] = np.nan
    if "chief_complain" in external.columns:
        external["chief_complain"] = (
            external["chief_complain"].fillna("").astype(str)
        )

    weights = [
        float(item.strip())
        for item in args.weights.split(",")
        if item.strip()
    ]

    results = []
    for weight in weights:
        if not (0 < weight <= 1):
            raise ValueError("External weights must be in (0, 1]")
        print(f"Running external_weight={weight:.3f}", flush=True)
        results.append(
            evaluate_weight(
                project,
                external,
                features=features,
                numeric_features=numeric_features,
                categorical_features=categorical_features,
                text_features=text_features,
                external_weight=weight,
            )
        )

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    summary = {
        "project_rows": int(len(project)),
        "external_rows_available": int(full_external_rows),
        "external_rows_used": int(len(external)),
        "external_class_counts_used": {
            label: int((external["label"] == label).sum())
            for label in LABELS
        },
        "external_feature_coverage": {
            "age": "available",
            "vital_signs": "not available",
            "pain_score": "not available",
            "chief_complain": "category only in source; intentionally omitted",
        },
        "evaluation_domain": "project-only 3-fold StratifiedKFold",
        "max_external_per_class": args.max_external_per_class,
        "results": results,
    }
    (output_dir / "results.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    rows = []
    for result in results:
        row = {"external_weight": result["external_weight"]}
        for prefix in ("baseline", "augmented", "delta"):
            for name, value in result[prefix].items():
                row[f"{prefix}_{name}"] = value
        rows.append(row)
    pd.DataFrame(rows).to_csv(output_dir / "summary.csv", index=False)

    baseline = results[0]["baseline"]
    lines = [
        "# Public KTAS Figshare augmentation benchmark",
        "",
        "Source: DOI 10.6084/m9.figshare.8044793.",
        "",
        "This external cohort uses direct KTAS labels, but the public workbook "
        "does not include the vital signs required by the production model. "
        "Only low-weight augmentation is tested, and all validation rows come "
        "from the project's original dataset.",
        "",
        f"- Project rows: {len(project)}",
        f"- External KTAS rows available: {full_external_rows}",
        f"- External rows used after class cap: {len(external)}",
        "- External class counts used: "
        + ", ".join(
            f"{label}={int((external['label'] == label).sum())}"
            for label in LABELS
        ),
        "",
        "## Baseline Random Forest",
        "",
        f"- Accuracy: {pct(baseline['accuracy'])}",
        f"- Macro F1: {pct(baseline['macro_f1'])}",
        f"- RED recall: {pct(baseline['red_recall'])}",
        f"- PINK recall: {pct(baseline['pink_recall'])}",
        f"- Route accuracy: {pct(baseline['route_accuracy'])}",
        f"- Severe under-triage: {pct(baseline['severe_undertriage_rate'])}",
        "",
        "## Low-weight augmentation sensitivity",
        "",
        "| KTAS external weight | Accuracy | Macro F1 | RED Recall | PINK Recall | Route Acc. | Severe under-triage | ΔRED | ΔPINK |",
        "|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]

    for result in results:
        aug = result["augmented"]
        delta = result["delta"]
        lines.append(
            f"| {result['external_weight']:.3f} "
            f"| {pct(aug['accuracy'])} "
            f"| {pct(aug['macro_f1'])} "
            f"| {pct(aug['red_recall'])} "
            f"| {pct(aug['pink_recall'])} "
            f"| {pct(aug['route_accuracy'])} "
            f"| {pct(aug['severe_undertriage_rate'])} "
            f"| {delta['red_recall'] * 100:+.2f} pp "
            f"| {delta['pink_recall'] * 100:+.2f} pp |"
        )

    lines += [
        "",
        "## Interpretation rule",
        "",
        "Because the external workbook lacks runtime vital signs, improvement "
        "must be treated cautiously. A production change is justified only if "
        "project-only high-acuity recall improves without degrading route "
        "accuracy or severe under-triage.",
    ]

    (output_dir / "README.md").write_text(
        "\n".join(lines),
        encoding="utf-8",
    )
    print((output_dir / "README.md").read_text(encoding="utf-8"))


if __name__ == "__main__":
    main()
