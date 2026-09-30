from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

from ai_triage.ml.train_dt import ktas_to_severity


MENTAL_TO_RUNTIME = {
    "A": 1.0,
    "ALERT": 1.0,
    "V": 2.0,
    "VERBAL": 2.0,
    "P": 3.0,
    "PAIN": 3.0,
    "U": 4.0,
    "UNRESPONSIVE": 4.0,
}

OUTPUT_COLUMNS = [
    "source",
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
    "chief_complain",
    "expected_resources",
    "label",
]


def _numeric(frame: pd.DataFrame, name: str) -> pd.Series:
    return pd.to_numeric(frame[name], errors="coerce")


def _plausible(
    frame: pd.DataFrame,
    name: str,
    low: float,
    high: float,
) -> pd.Series:
    values = _numeric(frame, name).astype(float)
    return values.where(values.between(low, high))


def _mental_status(series: pd.Series) -> pd.Series:
    normalized = series.fillna("").astype(str).str.strip().str.upper()
    mapped = normalized.map(MENTAL_TO_RUNTIME)

    # Keep numeric 1-4 encodings if present in a future copy of the file.
    numeric = pd.to_numeric(normalized, errors="coerce")
    numeric = numeric.where(numeric.between(1, 4))
    return mapped.fillna(numeric).astype(float)


def convert_excel(path: str | Path) -> pd.DataFrame:
    raw = pd.read_excel(path, sheet_name="Data")

    required = {
        "Age",
        "KTAS_value",
        "Mental state",
        "SBP",
        "DBP",
        "PR",
        "RR",
        "BT",
        "SpO2",
    }
    missing = required.difference(raw.columns)
    if missing:
        raise ValueError(
            "KTAS overcrowding workbook is missing columns: "
            + ", ".join(sorted(missing))
        )

    frame = pd.DataFrame(index=raw.index)
    frame["source"] = "KTAS_OVERcrowding_PLOS_0247042"
    frame["age"] = _plausible(raw, "Age", 0, 120)
    frame["nrs_pain"] = np.nan
    frame["rr"] = _plausible(raw, "RR", 4, 80)
    frame["pr"] = _plausible(raw, "PR", 20, 250)
    frame["sys_bp"] = _plausible(raw, "SBP", 40, 300)
    frame["dia_bp"] = _plausible(raw, "DBP", 20, 200)
    frame["bt"] = _plausible(raw, "BT", 25, 45)
    frame["o2sat"] = _plausible(raw, "SpO2", 50, 100)

    frame["mental_status"] = _mental_status(raw["Mental state"])
    frame["altered_mental_status"] = frame["mental_status"].where(
        frame["mental_status"].isna(),
        (frame["mental_status"] > 1).astype(float),
    )

    # These fields are unavailable before triage in the supporting workbook.
    # Do not derive them from admission/outcome/crowding variables because that
    # would introduce target leakage or features unavailable in production.
    for name in (
        "lifesaving_intervention",
        "high_risk_condition",
        "severe_distress",
        "expected_resources",
    ):
        frame[name] = np.nan

    # Complaint_category is an 8-class coded category, not the same free-text
    # chief complaint used by the deployed model. Leave it blank rather than
    # inventing vocabulary that production inputs will not contain.
    frame["chief_complain"] = ""
    frame["label"] = raw["KTAS_value"].apply(ktas_to_severity)

    frame = frame.dropna(subset=["label"]).copy()
    runtime_signal = frame[
        [
            "age",
            "rr",
            "pr",
            "sys_bp",
            "dia_bp",
            "bt",
            "o2sat",
            "mental_status",
        ]
    ].notna().any(axis=1)
    frame = frame[runtime_signal].copy()
    frame["label"] = frame["label"].astype(str)

    return frame[OUTPUT_COLUMNS].reset_index(drop=True)


def main():
    parser = argparse.ArgumentParser(
        description=(
            "Convert the public 73,776-row KTAS supporting dataset from "
            "PLOS ONE DOI 10.1371/journal.pone.0247042 into the project's "
            "runtime-compatible experimental schema."
        )
    )
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    converted = convert_excel(args.input)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    converted.to_csv(output, index=False)

    counts = converted["label"].value_counts().reindex(
        ["RED", "PINK", "YELLOW", "GREEN", "WHITE"],
        fill_value=0,
    )
    print(f"Saved {len(converted)} rows -> {output}")
    print("Class counts:")
    for label, count in counts.items():
        print(f"  {label}: {int(count)}")

    print("Runtime feature coverage:")
    for feature in (
        "age",
        "rr",
        "pr",
        "sys_bp",
        "dia_bp",
        "bt",
        "o2sat",
        "mental_status",
    ):
        present = int(converted[feature].notna().sum())
        print(f"  {feature}: {present}/{len(converted)}")

    print(
        "\nExcluded from model features: No_of_ER_patients, Overcrowding, "
        "Admission, Arrival time, Gender, Mode_of_arrival, and complaint "
        "category. They are either not current runtime features or could "
        "create target/domain leakage."
    )


if __name__ == "__main__":
    main()
