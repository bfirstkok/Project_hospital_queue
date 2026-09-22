from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from ai_triage.ml.import_nhamcs import convert_frame


class NhamcsImportTests(unittest.TestCase):
    def test_maps_immediacy_and_vitals(self):
        frame = pd.DataFrame(
            [
                {
                    "AGE": 67,
                    "PAINSCALE": 8,
                    "RESPR": 30,
                    "PULSE": 125,
                    "BPSYS": 92,
                    "BPDIAS": 58,
                    "TEMPF": 986,
                    "POPCT": 89,
                    "IMMEDR": 1,
                },
                {
                    "AGE": 35,
                    "PAINSCALE": 3,
                    "RESPR": 18,
                    "PULSE": 88,
                    "BPSYS": 124,
                    "BPDIAS": 76,
                    "TEMPF": 98.6,
                    "POPCT": 98,
                    "IMMEDR": 2,
                },
            ]
        )

        converted = convert_frame(frame, 2021)

        self.assertEqual(converted["label"].tolist(), ["RED", "PINK"])
        self.assertEqual(converted["source_year"].tolist(), [2021, 2021])
        self.assertAlmostEqual(float(converted.loc[0, "bt"]), 37.0, places=1)
        self.assertEqual(float(converted.loc[0, "o2sat"]), 89.0)
        self.assertEqual(float(converted.loc[0, "nrs_pain"]), 8.0)
        self.assertEqual(converted.loc[0, "chief_complain"], "")

    def test_filters_nontriaged_and_special_values(self):
        frame = pd.DataFrame(
            [
                {
                    "AGE": 45,
                    "PAINSCALE": 99,
                    "RESPR": -9,
                    "PULSE": 998,
                    "BPSYS": 140,
                    "BPDIAS": 90,
                    "TEMPF": -9,
                    "POPOCT": 97,
                    "IMMEDR": 3,
                },
                {
                    "AGE": 50,
                    "PAINSCALE": 4,
                    "RESPR": 20,
                    "PULSE": 90,
                    "BPSYS": 120,
                    "BPDIAS": 80,
                    "TEMPF": 99.0,
                    "POPOCT": 96,
                    "IMMEDR": 7,
                },
            ]
        )

        converted = convert_frame(frame, 2022)

        self.assertEqual(len(converted), 1)
        self.assertEqual(converted.loc[0, "label"], "YELLOW")
        self.assertTrue(np.isnan(converted.loc[0, "nrs_pain"]))
        self.assertTrue(np.isnan(converted.loc[0, "rr"]))
        self.assertTrue(np.isnan(converted.loc[0, "pr"]))
        self.assertEqual(float(converted.loc[0, "o2sat"]), 97.0)


if __name__ == "__main__":
    unittest.main()
