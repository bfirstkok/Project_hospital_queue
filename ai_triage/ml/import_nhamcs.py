from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd


IMMEDIACY_TO_LABEL = {
    1: "RED",
    2: "PINK",
    3: "YELLOW",
    4: "GREEN",
    5: "WHITE",
}

OUTPUT_COLUMNS = [
    "source",
    "source_year",
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


def _column(frame: pd.DataFrame, *names: str) -> pd.Series:
    lookup = {str(col).upper(): col for col in frame.columns}
    for name in names:
        actual = lookup.get(name.upper())
        if actual is not None:
            return frame[actual]
    return pd.Series(np.nan, index=frame.index)


def _numeric(series: pd.Series) -> pd.Series:
    if pd.api.types.is_categorical_dtype(series.dtype):
        series = series.astype(object)
    return pd.to_numeric(series, errors="coerce")


def _clean_temperature_f(series: pd.Series) -> pd.Series:
    values = _numeric(series).astype(float)

    # ASCII-derived values may use the implied decimal (e.g. 986 = 98.6 F),
    # while pre-made Stata files may already contain 98.6.
    implied_decimal = values.between(200, 1200)
    values.loc[implied_decimal] = values.loc[implied_decimal] / 10.0

    celsius = values.copy()
    fahrenheit = values.between(45, 130)
    celsius.loc[fahrenheit] = (
        (values.loc[fahrenheit] - 32.0) * 5.0 / 9.0
    )

    return celsius.where(celsius.between(25, 45)).round(2)


def _clean_range(
    series: pd.Series,
    low: float,
    high: float,
    *,
    invalid: set[float] | None = None,
) -> pd.Series:
    values = _numeric(series).astype(float)
    if invalid:
        values = values.mask(values.isin(invalid))
    return values.where(values.between(low, high))


def convert_frame(frame: pd.DataFrame, year: int) -> pd.DataFrame:
    immed = _numeric(_column(frame, "IMMEDR"))
    valid = immed.isin(IMMEDIACY_TO_LABEL)

    frame = frame.loc[valid].reset_index(drop=True)
    immed = immed.loc[valid].astype(int).reset_index(drop=True)

    out = pd.DataFrame(index=frame.index)
    out["source"] = "NHAMCS"
    out["source_year"] = int(year)
    out["age"] = _clean_range(_column(frame, "AGE"), 0, 120)
    out["nrs_pain"] = _clean_range(
        _column(frame, "PAINSCALE"),
        0,
        10,
        invalid={-9, -8, 99},
    )
    out["rr"] = _clean_range(
        _column(frame, "RESPR"),
        4,
        80,
        invalid={-9, 998, 999},
    )
    out["pr"] = _clean_range(
        _column(frame, "PULSE"),
        20,
        250,
        invalid={-9, 998, 999},
    )
    out["sys_bp"] = _clean_range(
        _column(frame, "BPSYS"),
        40,
        300,
        invalid={-9, 998, 999},
    )
    out["dia_bp"] = _clean_range(
        _column(frame, "BPDIAS"),
        20,
        200,
        invalid={-9, 998, 999},
    )
    out["bt"] = _clean_temperature_f(_column(frame, "TEMPF"))
    out["o2sat"] = _clean_range(
        _column(frame, "POPCT", "POPOCT"),
        50,
        100,
        invalid={-9, 998, 999},
    )

    # These project-specific fields are not directly represented by the
    # NHAMCS public-use variables used here. Keep them missing rather than
    # manufacture clinical findings.
    for name in (
        "lifesaving_intervention",
        "high_risk_condition",
        "altered_mental_status",
        "mental_status",
        "severe_distress",
        "expected_resources",
    ):
        out[name] = np.nan

    # NHAMCS provides coded Reason-for-Visit fields rather than the same
    # free-text chief complaint used by the project. Leave text blank to avoid
    # teaching the model code tokens that production will never receive.
    out["chief_complain"] = ""
    out["label"] = immed.map(IMMEDIACY_TO_LABEL)

    signal = out[
        ["age", "nrs_pain", "rr", "pr", "sys_bp", "dia_bp", "bt", "o2sat"]
    ].notna().any(axis=1)
    return out.loc[signal, OUTPUT_COLUMNS].reset_index(drop=True)


def read_stata(path: str | Path) -> pd.DataFrame:
    return pd.read_stata(
        path,
        convert_categoricals=False,
        preserve_dtypes=False,
    )


def parse_input(value: str) -> tuple[int, Path]:
    if "=" not in value:
        raise argparse.ArgumentTypeError(
            "--input must use YEAR=/path/to/file.dta"
        )
    year_text, path_text = value.split("=", 1)
    try:
        year = int(year_text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("YEAR must be an integer") from exc
    return year, Path(path_text)


def main():
    parser = argparse.ArgumentParser(
        description=(
            "Convert public-use NHAMCS ED Stata files to the runtime-compatible "
            "schema used by the triage ML experiments."
        )
    )
    parser.add_argument(
        "--input",
        action="append",
        required=True,
        type=parse_input,
        metavar="YEAR=FILE.dta",
    )
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    converted = []
    for year, path in args.input:
        frame = read_stata(path)
        year_frame = convert_frame(frame, year)
        converted.append(year_frame)
        counts = year_frame["label"].value_counts()
        print(
            f"{year}: rows={len(year_frame)} "
            + " ".join(
                f"{label}={int(counts.get(label, 0))}"
                for label in IMMEDIACY_TO_LABEL.values()
            )
        )

    combined = pd.concat(converted, ignore_index=True, sort=False)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    combined.to_csv(output, index=False)

    print(f"\nSaved {len(combined)} rows -> {output}")
    print("Combined class counts:")
    counts = combined["label"].value_counts()
    for label in IMMEDIACY_TO_LABEL.values():
        print(f"  {label}: {int(counts.get(label, 0))}")

    print(
        "\nNOTE: NHAMCS IMMEDR 1-5 (Immediate to Nonurgent) is harmonized "
        "to RED-PINK-YELLOW-GREEN-WHITE for an external-data experiment. "
        "It is not asserted to be clinically identical to KTAS."
    )


if __name__ == "__main__":
    main()
