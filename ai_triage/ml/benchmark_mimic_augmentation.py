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
    build_model,
    load_clean_dataset,
    load_local_confirmed_dataset,
)


def route_accuracy(y_true, y_pred) -> float:
    true_route = [ROUTE_GROUP[str(label)] for label in y_true]
    pred_route = [ROUTE_GROUP[str(label)] for label in y_pred]
    return accuracy_score(true_route, pred_route)


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
        "route_accuracy": route_accuracy(y_true, y_pred),
    }


def sample_external(
    external: pd.DataFrame,
    *,
    max_per_class: int,
    random_state: int,
) -> pd.DataFrame:
    sampled = []
    for label in LABELS:
        group = external[external["label"] == label]
        if group.empty:
            continue
        if max_per_class > 0 and len(group) > max_per_class:
            group = group.sample(
                n=max_per_class,
                random_state=random_state,
            )
        sampled.append(group)
    if not sampled:
        raise ValueError("No valid MIMIC rows remain after filtering")
    return pd.concat(sampled, ignore_index=True)


def load_external(path: str | Path) -> pd.DataFrame:
    frame = pd.read_csv(path, low_memory=False)
    if "label" not in frame.columns:
        raise ValueError("Converted MIMIC CSV must contain label")
    frame["label"] = frame["label"].astype(str).str.upper().str.strip()
    frame = frame[frame["label"].isin(LABELS)].copy()
    return frame


def main():
    parser = argparse.ArgumentParser(
        description=(
            "Compare the current Random Forest trained on project data alone "
            "versus project data augmented with converted MIMIC-IV-ED. "
            "Evaluation is always on held-out project-domain rows."
        )
    )
    parser.add_argument("--mimic-csv", required=True)
    parser.add_argument(
        "--max-external-per-class",
        type=int,
        default=5000,
        help=(
            "Maximum MIMIC rows sampled per severity class per run. "
            "0 means no cap."
        ),
    )
    parser.add_argument(
        "--external-weight",
        type=float,
        default=0.35,
        help=(
            "Training sample weight assigned to MIMIC rows relative to "
            "project rows (=1.0)."
        ),
    )
    parser.add_argument(
        "--output",
        default="ai_triage/reports/mimic_augmentation.json",
    )
    args = parser.parse_args()

    if not (0 < args.external_weight <= 1):
        raise ValueError("--external-weight must be in (0, 1]")

    reference = load_clean_dataset()
    local = load_local_confirmed_dataset()
    project = pd.concat([reference, local], ignore_index=True, sort=False)
    external = load_external(args.mimic_csv)

    numeric_features = [
        feature
        for feature in RUNTIME_NUMERIC_FEATURES
        if feature in project.columns
    ]
    categorical_features = [
        feature
        for feature in RUNTIME_CATEGORICAL_FEATURES
        if feature in project.columns
    ]
    text_features = [
        feature
        for feature in RUNTIME_TEXT_FEATURES
        if feature in project.columns
    ]
    features = numeric_features + categorical_features + text_features

    for feature in features:
        if feature not in external.columns:
            external[feature] = np.nan

    external = sample_external(
        external,
        max_per_class=args.max_external_per_class,
        random_state=42,
    )

    X_project = project[features].copy()
    y_project = project["label"].astype(str).to_numpy()

    cv = StratifiedKFold(n_splits=3, shuffle=True, random_state=42)
    baseline_predictions = np.empty(len(project), dtype=object)
    augmented_predictions = np.empty(len(project), dtype=object)

    fold_rows = []

    for fold, (train_idx, test_idx) in enumerate(
        cv.split(X_project, y_project),
        start=1,
    ):
        X_train = X_project.iloc[train_idx].copy()
        y_train = y_project[train_idx]
        X_test = X_project.iloc[test_idx].copy()
        y_test = y_project[test_idx]

        baseline_model = build_model(
            numeric_features,
            categorical_features,
            text_features,
        )
        baseline_model.fit(X_train, y_train)
        baseline_pred = baseline_model.predict(X_test)
        baseline_predictions[test_idx] = baseline_pred

        X_external = external[features].copy()
        y_external = external["label"].astype(str).to_numpy()

        X_augmented = pd.concat(
            [X_train, X_external],
            ignore_index=True,
            sort=False,
        )
        y_augmented = np.concatenate([y_train, y_external])
        weights = np.concatenate(
            [
                np.ones(len(y_train), dtype=float),
                np.full(
                    len(y_external),
                    args.external_weight,
                    dtype=float,
                ),
            ]
        )

        augmented_model = build_model(
            numeric_features,
            categorical_features,
            text_features,
        )
        augmented_model.fit(
            X_augmented,
            y_augmented,
            classifier__sample_weight=weights,
        )
        augmented_pred = augmented_model.predict(X_test)
        augmented_predictions[test_idx] = augmented_pred

        fold_rows.append(
            {
                "fold": fold,
                "project_train_rows": int(len(train_idx)),
                "project_test_rows": int(len(test_idx)),
                "external_rows": int(len(external)),
                "baseline": metrics(y_test, baseline_pred),
                "augmented": metrics(y_test, augmented_pred),
            }
        )

    baseline = metrics(y_project, baseline_predictions)
    augmented = metrics(y_project, augmented_predictions)
    delta = {
        key: augmented[key] - baseline[key]
        for key in baseline
    }

    result = {
        "project_rows": int(len(project)),
        "mimic_rows_available_after_conversion": int(
            len(load_external(args.mimic_csv))
        ),
        "mimic_rows_used": int(len(external)),
        "mimic_class_counts_used": {
            label: int((external["label"] == label).sum())
            for label in LABELS
        },
        "max_external_per_class": args.max_external_per_class,
        "external_weight": args.external_weight,
        "evaluation_domain": "project-only held-out folds",
        "baseline": baseline,
        "augmented": augmented,
        "delta": delta,
        "folds": fold_rows,
    }

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print("Project-only validation results")
    print(f"Project rows: {len(project)}")
    print(f"MIMIC rows used: {len(external)}")
    print(f"External weight: {args.external_weight}")
    print("")
    for key in baseline:
        print(
            f"{key:20s} baseline={baseline[key]:.4f} "
            f"augmented={augmented[key]:.4f} "
            f"delta={delta[key]:+.4f}"
        )
    print(f"\nSaved: {output}")


if __name__ == "__main__":
    main()
