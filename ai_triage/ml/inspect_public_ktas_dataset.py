from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import pandas as pd


def load_table(path: Path) -> pd.DataFrame:
    suffix = path.suffix.lower()
    if suffix in {".csv", ".txt"}:
        return pd.read_csv(path, low_memory=False)
    if suffix in {".xlsx", ".xls"}:
        return pd.read_excel(path)
    if suffix == ".sav":
        return pd.read_spss(path)
    if suffix == ".dta":
        return pd.read_stata(path, convert_categoricals=False)
    raise ValueError(f"Unsupported dataset file: {path.name}")


def normalize_name(name: str) -> str:
    return "".join(ch.lower() for ch in str(name) if ch.isalnum())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-dir", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    root = Path(args.input_dir)
    candidates = []
    for path in sorted(root.rglob("*")):
        if path.is_file() and path.suffix.lower() in {
            ".csv", ".txt", ".xlsx", ".xls", ".sav", ".dta"
        }:
            candidates.append(path)

    report = {"files": []}
    print(f"Tabular files discovered: {len(candidates)}")

    for path in candidates:
        print(f"\n=== {path.name} ===")
        try:
            frame = load_table(path)
        except Exception as exc:
            print(f"FAILED TO READ: {type(exc).__name__}: {exc}")
            report["files"].append(
                {"file": path.name, "error": f"{type(exc).__name__}: {exc}"}
            )
            continue

        columns = [str(col) for col in frame.columns]
        normalized = {normalize_name(col): col for col in columns}
        print(f"rows={len(frame)} cols={len(columns)}")
        print("columns:")
        for col in columns:
            print(f"  - {col}")

        ktas_candidates = [
            col for col in columns
            if "ktas" in normalize_name(col)
            or normalize_name(col) in {"triage", "acuity", "severity", "level"}
        ]
        pain_candidates = [
            col for col in columns
            if "pain" in normalize_name(col)
        ]
        vital_candidates = [
            col for col in columns
            if any(
                token in normalize_name(col)
                for token in (
                    "age", "heartrate", "pulse", "hr", "resp", "rr",
                    "sbp", "dbp", "bloodpressure", "temperature", "temp",
                    "spo2", "o2sat", "saturation"
                )
            )
        ]

        print("KTAS-like columns:", ktas_candidates)
        print("Pain columns:", pain_candidates)
        print("Vital-like columns:", vital_candidates)

        value_counts = {}
        for col in ktas_candidates[:5]:
            counts = (
                frame[col]
                .value_counts(dropna=False)
                .head(20)
                .to_dict()
            )
            value_counts[col] = {str(k): int(v) for k, v in counts.items()}
            print(f"value_counts[{col}]={value_counts[col]}")

        report["files"].append(
            {
                "file": path.name,
                "rows": int(len(frame)),
                "columns": columns,
                "ktas_like_columns": ktas_candidates,
                "pain_columns": pain_candidates,
                "vital_like_columns": vital_candidates,
                "value_counts": value_counts,
            }
        )

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"\nSaved inspection report -> {output}")


if __name__ == "__main__":
    main()
