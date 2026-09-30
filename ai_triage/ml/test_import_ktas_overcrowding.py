from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

from ai_triage.ml.import_ktas_overcrowding import convert_excel


class KtasOvercrowdingImportTests(unittest.TestCase):
    def test_maps_direct_ktas_labels_vitals_and_mental_state(self):
        frame = pd.DataFrame(
            [
                {
                    "Age": 84,
                    "KTAS_value": 1,
                    "Mental state": "A",
                    "SBP": 115,
                    "DBP": 50,
                    "PR": 60,
                    "RR": 20,
                    "BT": 36.2,
                    "SpO2": 97,
                },
                {
                    "Age": 50,
                    "KTAS_value": 2,
                    "Mental state": "V",
                    "SBP": 90,
                    "DBP": 55,
                    "PR": 120,
                    "RR": 30,
                    "BT": 38.5,
                    "SpO2": 91,
                },
            ]
        )

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ktas.xlsx"
            with pd.ExcelWriter(path) as writer:
                frame.to_excel(writer, sheet_name="Data", index=False)
            converted = convert_excel(path)

        self.assertEqual(converted["label"].tolist(), ["RED", "PINK"])
        self.assertEqual(converted["age"].tolist(), [84, 50])
        self.assertEqual(converted["mental_status"].tolist(), [1.0, 2.0])
        self.assertEqual(converted["altered_mental_status"].tolist(), [0.0, 1.0])
        self.assertEqual(converted["sys_bp"].tolist(), [115.0, 90.0])
        self.assertEqual(converted["o2sat"].tolist(), [97.0, 91.0])
        self.assertTrue(converted["nrs_pain"].isna().all())
        self.assertTrue((converted["chief_complain"] == "").all())

    def test_drops_invalid_label_and_rejects_implausible_vitals(self):
        frame = pd.DataFrame(
            [
                {
                    "Age": 40,
                    "KTAS_value": 3,
                    "Mental state": "P",
                    "SBP": 500,
                    "DBP": 80,
                    "PR": 90,
                    "RR": 18,
                    "BT": 36.5,
                    "SpO2": 98,
                },
                {
                    "Age": 50,
                    "KTAS_value": 9,
                    "Mental state": "A",
                    "SBP": 120,
                    "DBP": 80,
                    "PR": 90,
                    "RR": 18,
                    "BT": 36.5,
                    "SpO2": 98,
                },
            ]
        )

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ktas.xlsx"
            with pd.ExcelWriter(path) as writer:
                frame.to_excel(writer, sheet_name="Data", index=False)
            converted = convert_excel(path)

        self.assertEqual(len(converted), 1)
        self.assertEqual(converted.loc[0, "label"], "YELLOW")
        self.assertTrue(np.isnan(converted.loc[0, "sys_bp"]))
        self.assertEqual(converted.loc[0, "mental_status"], 3.0)


if __name__ == "__main__":
    unittest.main()
