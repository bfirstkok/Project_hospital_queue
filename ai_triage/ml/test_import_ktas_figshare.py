from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

from ai_triage.ml.import_ktas_figshare import convert_excel


class KtasFigshareImportTests(unittest.TestCase):
    def test_maps_direct_ktas_labels_and_keeps_unavailable_vitals_missing(self):
        sheet1 = pd.DataFrame(
            [
                {"KTAS Level": 1, "Age": 70, "Group": 1},
                {"KTAS Level": 2, "Age": 55, "Group": 0},
                {"KTAS Level": 5, "Age": 22, "Group": 1},
            ]
        )
        sheet2 = pd.DataFrame(
            {
                "Variable": ["KTAS Level"],
                "Description of variable values": ["Level of KTAS"],
            }
        )

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ktas.xlsx"
            with pd.ExcelWriter(path) as writer:
                sheet1.to_excel(writer, sheet_name="Sheet1", index=False)
                sheet2.to_excel(writer, sheet_name="Sheet2", index=False)

            converted = convert_excel(path)

        self.assertEqual(converted["label"].tolist(), ["RED", "PINK", "WHITE"])
        self.assertEqual(converted["age"].tolist(), [70, 55, 22])
        self.assertTrue(converted["rr"].isna().all())
        self.assertTrue(converted["o2sat"].isna().all())
        self.assertTrue((converted["chief_complain"] == "").all())

    def test_drops_rows_without_valid_ktas_or_age(self):
        sheet1 = pd.DataFrame(
            [
                {"KTAS Level": 3, "Age": 40},
                {"KTAS Level": 9, "Age": 50},
                {"KTAS Level": 2, "Age": np.nan},
            ]
        )

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ktas.xlsx"
            with pd.ExcelWriter(path) as writer:
                sheet1.to_excel(writer, sheet_name="Sheet1", index=False)
            converted = convert_excel(path)

        self.assertEqual(len(converted), 1)
        self.assertEqual(converted.loc[0, "label"], "YELLOW")


if __name__ == "__main__":
    unittest.main()
