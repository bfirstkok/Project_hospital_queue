from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import pandas as pd

from ai_triage.ml.import_mimic_iv_ed import convert


class MimicIvEdImportTests(unittest.TestCase):
    def test_maps_acuity_and_runtime_fields(self):
        rows = pd.DataFrame(
            [
                {
                    "subject_id": 1,
                    "stay_id": 101,
                    "temperature": 98.6,
                    "heartrate": 120,
                    "resprate": 28,
                    "o2sat": 89,
                    "sbp": 92,
                    "dbp": 58,
                    "pain": "8",
                    "acuity": 1,
                    "chiefcomplaint": "shortness of breath",
                },
                {
                    "subject_id": 2,
                    "stay_id": 102,
                    "temperature": 37.0,
                    "heartrate": 88,
                    "resprate": 18,
                    "o2sat": 98,
                    "sbp": 124,
                    "dbp": 76,
                    "pain": "2/10",
                    "acuity": 2,
                    "chiefcomplaint": "chest pain",
                },
            ]
        )

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "triage.csv"
            rows.to_csv(path, index=False)
            converted = convert(path)

        self.assertEqual(converted["label"].tolist(), ["RED", "PINK"])
        self.assertAlmostEqual(float(converted.loc[0, "bt"]), 37.0, places=1)
        self.assertEqual(float(converted.loc[0, "o2sat"]), 89.0)
        self.assertEqual(float(converted.loc[1, "nrs_pain"]), 2.0)
        self.assertEqual(converted.loc[1, "chief_complain"], "chest pain")

    def test_high_acuity_and_one_stay_per_patient(self):
        rows = pd.DataFrame(
            [
                {
                    "subject_id": 7,
                    "stay_id": 701,
                    "temperature": 37.0,
                    "heartrate": 80,
                    "resprate": 18,
                    "o2sat": 98,
                    "sbp": 120,
                    "dbp": 70,
                    "pain": "1",
                    "acuity": 4,
                    "chiefcomplaint": "minor injury",
                },
                {
                    "subject_id": 7,
                    "stay_id": 702,
                    "temperature": 38.0,
                    "heartrate": 130,
                    "resprate": 30,
                    "o2sat": 90,
                    "sbp": 90,
                    "dbp": 55,
                    "pain": "9",
                    "acuity": 1,
                    "chiefcomplaint": "respiratory distress",
                },
                {
                    "subject_id": 8,
                    "stay_id": 801,
                    "temperature": 36.8,
                    "heartrate": 108,
                    "resprate": 23,
                    "o2sat": 95,
                    "sbp": 110,
                    "dbp": 68,
                    "pain": "7",
                    "acuity": 2,
                    "chiefcomplaint": "abdominal pain",
                },
            ]
        )

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "triage.csv"
            rows.to_csv(path, index=False)
            converted = convert(
                path,
                high_acuity_only=True,
                one_stay_per_patient=True,
            )

        self.assertEqual(len(converted), 2)
        self.assertEqual(set(converted["label"]), {"RED", "PINK"})
        self.assertEqual(set(converted["subject_id"]), {7, 8})


if __name__ == "__main__":
    unittest.main()
