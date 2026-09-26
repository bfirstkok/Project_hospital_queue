from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

from ai_triage.ml.train_dt import ktas_to_severity


COMPLAINT_CATEGORY = {
    1: "Gastrointestinal",
    2: "Respiratory",
    3: "Cardiovascular",
    4: "Neurological",
    5: "Musculoskeletal",
    6: "Skin",
    7: "General",
    8: "Others",
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


def convert_excel(path: str | Path) -> pd.DataFrame:
    raw = pd.read_excel(path, sheet_name="Sheet1")

    required = {"KTAS Level", "Age"}
    missing = required.difference(raw.columns)
    if missing:
        raise ValueError(
            "KTAS Figshare workbook is missing columns: "
            + ", ".join(sorted(missing))
        )

    frame = pd.DataFrame(index=raw.index)
    frame["source"] = "KTAS_FIGSHARE_8044793"
    frame["age"] = pd.to_numeric(raw["Age"], errors="coerce").where(
        lambda s: s.between(0, 120)
    )

    # This public KTAS dataset does not contain the initial vital signs used by
    # the production model. Keep those fields missing rather than fabricating
    # physiologic measurements.
    for column in (
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
        "expected_resources",
    ):
        frame[column] = np.nan

    # The dataset contains only an 8-class complaint category, not the same
    # free-text chief complaint available to the production model. Keep text
    # blank in the augmentation benchmark to avoid introducing artificial
    # vocabulary that production encounters will not contain.
    frame["chief_complain"] = ""
    frame["label"] = raw["KTAS Level"].apply(ktas_to_severity)

    frame = frame.dropna(subset=["label", "age"]).copy()
    frame["label"] = frame["label"].astype(str)
    return frame[OUTPUT_COLUMNS].reset_index(drop=True)


def main():
    parser = argparse.ArgumentParser(
        description=(
            "Convert the public Figshare KTAS workbook (DOI "
            "10.6084/m9.figshare.8044793) into the project's experimental "
            "runtime schema."
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
    for label, count in counts.items():
        print(f"{label}: {int(count)}")

    print(
        "\nFeature coverage warning: this external KTAS dataset provides "
        "direct KTAS labels and age, but not the vital signs required by the "
        "production model. It should only be used as low-weight augmentation "
        "in a project-only validation experiment."
    )


if __name__ == "__main__":
    main()
