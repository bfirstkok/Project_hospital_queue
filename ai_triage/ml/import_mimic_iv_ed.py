from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd


ACUITY_TO_LABEL = {
    1: "RED",
    2: "PINK",
    3: "YELLOW",
    4: "GREEN",
    5: "WHITE",
}

OUTPUT_COLUMNS = [
    "source",
    "subject_id",
    "stay_id",
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


def read_table(path: str | Path) -> pd.DataFrame:
    return pd.read_csv(path, low_memory=False)


def numeric(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce")


def normalize_temperature(series: pd.Series) -> pd.Series:
    values = numeric(series).astype(float)
    converted = values.copy()
    fahrenheit_mask = values > 45
    converted.loc[fahrenheit_mask] = (
        (values.loc[fahrenheit_mask] - 32.0) * 5.0 / 9.0
    )
    return converted.where(converted.between(25.0, 45.0)).round(2)


def normalize_pain(series: pd.Series) -> pd.Series:
    extracted = (
        series.fillna("")
        .astype(str)
        .str.extract(r"(?<!\d)(10|[0-9])(?:\.0)?(?!\d)", expand=False)
    )
    values = pd.to_numeric(extracted, errors="coerce")
    return values.where(values.between(0, 10))


def attach_age(
    triage: pd.DataFrame,
    *,
    edstays_path: str | Path | None = None,
    patients_path: str | Path | None = None,
) -> pd.Series:
    if not edstays_path or not patients_path:
        return pd.Series(np.nan, index=triage.index, dtype=float)

    edstays = read_table(edstays_path)
    patients = read_table(patients_path)

    required_ed = {"stay_id", "subject_id", "intime"}
    required_patients = {"subject_id", "anchor_age", "anchor_year"}
    missing_ed = required_ed.difference(edstays.columns)
    missing_patients = required_patients.difference(patients.columns)
    if missing_ed:
        raise ValueError(
            "edstays is missing: " + ", ".join(sorted(missing_ed))
        )
    if missing_patients:
        raise ValueError(
            "patients is missing: " + ", ".join(sorted(missing_patients))
        )

    stay_demo = edstays[["stay_id", "subject_id", "intime"]].copy()
    stay_demo["intime"] = pd.to_datetime(
        stay_demo["intime"],
        errors="coerce",
    )
    stay_demo["visit_year"] = stay_demo["intime"].dt.year

    patient_demo = patients[
        ["subject_id", "anchor_age", "anchor_year"]
    ].copy()

    merged = (
        triage[["stay_id", "subject_id"]]
        .merge(stay_demo, on=["stay_id", "subject_id"], how="left")
        .merge(patient_demo, on="subject_id", how="left")
    )

    age = (
        numeric(merged["anchor_age"])
        + numeric(merged["visit_year"])
        - numeric(merged["anchor_year"])
    )
    return age.where(age.between(0, 120)).reset_index(drop=True)


def convert(
    triage_path: str | Path,
    *,
    edstays_path: str | Path | None = None,
    patients_path: str | Path | None = None,
    high_acuity_only: bool = False,
    one_stay_per_patient: bool = False,
) -> pd.DataFrame:
    raw = read_table(triage_path)

    required = {
        "subject_id",
        "stay_id",
        "temperature",
        "heartrate",
        "resprate",
        "o2sat",
        "sbp",
        "dbp",
        "pain",
        "acuity",
        "chiefcomplaint",
    }
    missing = required.difference(raw.columns)
    if missing:
        raise ValueError(
            "MIMIC-IV-ED triage file is missing columns: "
            + ", ".join(sorted(missing))
        )

    acuity = numeric(raw["acuity"])
    valid = acuity.isin(ACUITY_TO_LABEL)
    raw = raw.loc[valid].reset_index(drop=True)
    acuity = acuity.loc[valid].astype(int).reset_index(drop=True)

    output = pd.DataFrame(index=raw.index)
    output["source"] = "MIMIC-IV-ED"
    output["subject_id"] = raw["subject_id"]
    output["stay_id"] = raw["stay_id"]
    output["age"] = attach_age(
        raw,
        edstays_path=edstays_path,
        patients_path=patients_path,
    )
    output["nrs_pain"] = normalize_pain(raw["pain"])
    output["rr"] = numeric(raw["resprate"]).where(
        numeric(raw["resprate"]).between(4, 80)
    )
    output["pr"] = numeric(raw["heartrate"]).where(
        numeric(raw["heartrate"]).between(20, 250)
    )
    output["sys_bp"] = numeric(raw["sbp"]).where(
        numeric(raw["sbp"]).between(40, 300)
    )
    output["dia_bp"] = numeric(raw["dbp"]).where(
        numeric(raw["dbp"]).between(20, 200)
    )
    output["bt"] = normalize_temperature(raw["temperature"])
    output["o2sat"] = numeric(raw["o2sat"]).where(
        numeric(raw["o2sat"]).between(50, 100)
    )

    # MIMIC-IV-ED triage does not directly provide these project-specific
    # runtime fields. Keep them missing instead of inferring clinical findings.
    for column in (
        "lifesaving_intervention",
        "high_risk_condition",
        "altered_mental_status",
        "mental_status",
        "severe_distress",
        "expected_resources",
    ):
        output[column] = np.nan

    output["chief_complain"] = (
        raw["chiefcomplaint"].fillna("").astype(str).str.strip()
    )
    output["label"] = acuity.map(ACUITY_TO_LABEL)

    if high_acuity_only:
        output = output[output["label"].isin(["RED", "PINK"])].copy()

    if one_stay_per_patient:
        severity_rank = output["label"].map(
            {"RED": 1, "PINK": 2, "YELLOW": 3, "GREEN": 4, "WHITE": 5}
        )
        output = (
            output.assign(_severity_rank=severity_rank)
            .sort_values(["subject_id", "_severity_rank", "stay_id"])
            .drop_duplicates("subject_id", keep="first")
            .drop(columns="_severity_rank")
        )

    usable_signal = (
        output[
            ["nrs_pain", "rr", "pr", "sys_bp", "dia_bp", "bt", "o2sat"]
        ]
        .notna()
        .any(axis=1)
        | output["chief_complain"].ne("")
    )
    output = output[usable_signal].copy()

    return output[OUTPUT_COLUMNS].reset_index(drop=True)


def main():
    parser = argparse.ArgumentParser(
        description=(
            "Convert credentialed MIMIC-IV-ED triage data into this project's "
            "runtime-compatible experimental schema."
        )
    )
    parser.add_argument("--triage", required=True)
    parser.add_argument("--edstays")
    parser.add_argument("--patients")
    parser.add_argument("--output", required=True)
    parser.add_argument(
        "--high-acuity-only",
        action="store_true",
        help="Keep only acuity 1-2 as experimental RED/PINK rows.",
    )
    parser.add_argument(
        "--one-stay-per-patient",
        action="store_true",
        help=(
            "Keep one most-severe ED stay per subject to reduce patient-level "
            "cross-validation leakage."
        ),
    )
    args = parser.parse_args()

    converted = convert(
        args.triage,
        edstays_path=args.edstays,
        patients_path=args.patients,
        high_acuity_only=args.high_acuity_only,
        one_stay_per_patient=args.one_stay_per_patient,
    )

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    converted.to_csv(output_path, index=False)

    counts = converted["label"].value_counts().reindex(
        ["RED", "PINK", "YELLOW", "GREEN", "WHITE"],
        fill_value=0,
    )
    print(f"Saved {len(converted)} rows -> {output_path}")
    print("Class counts:")
    for label, count in counts.items():
        print(f"  {label}: {int(count)}")

    print(
        "\nWARNING: MIMIC-IV-ED acuity 1-5 and KTAS 1-5 are different "
        "triage systems. This direct color mapping is for controlled "
        "experiments only and requires domain-shift validation before any "
        "production use."
    )
    print(
        "Do not commit raw MIMIC data, derived row-level datasets, or trained "
        "artifacts to a public repository. Follow the PhysioNet DUA."
    )


if __name__ == "__main__":
    main()
