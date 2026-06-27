"""Regression tests for the dofus-map count decoder.

The crucial format detail (the "space enigma"): within a coordspec ``x:y1 y2 y3``
a single x is followed by several space-separated y values, all sharing the
group's count. The decoder must not drop the post-space coords.
"""

from __future__ import annotations

import importlib.util
import os
import unittest

# Load the script module directly (scripts/ is not a package).
_SPEC = importlib.util.spec_from_file_location(
    "build_dofusmap_counts",
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                 "scripts", "build_dofusmap_counts.py"),
)
dm = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(dm)


class DecodeTest(unittest.TestCase):
    def test_simple_group(self):
        # 6 units at each of the four coords.
        got = dm.decode('"27&0&6*0:25+2:24+3:24+7:9"')
        self.assertEqual(got, {(0, 25): 6, (2, 24): 6, (3, 24): 6, (7, 9): 6})

    def test_space_means_extra_y_same_x(self):
        # `4*0:9 -21+3:22 31` -> (0,9),(0,-21),(3,22),(3,31), all count 4.
        got = dm.decode('"47&0&4*0:9 -21+3:22 31+13:20"')
        self.assertEqual(got, {(0, 9): 4, (0, -21): 4, (3, 22): 4,
                               (3, 31): 4, (13, 20): 4})

    def test_negative_coords_and_multiple_groups(self):
        got = dm.decode('"68&0&21*-1:-42_5*9:-19 -17"')
        self.assertEqual(got, {(-1, -42): 21, (9, -19): 5, (9, -17): 5})

    def test_same_coord_accumulates_across_groups(self):
        got = dm.decode('"1&0&3*5:5_2*5:5"')
        self.assertEqual(got, {(5, 5): 5})

    def test_empty_and_malformed_are_tolerated(self):
        self.assertEqual(dm.decode('"9&0&"'), {})
        self.assertEqual(dm.decode("garbage"), {})

    def test_slug_normalizes_accents(self):
        self.assertEqual(dm.slug("Frêne"), "frene")
        self.assertEqual(dm.slug("Carpe d'Iem"), "carpe_d_iem")


if __name__ == "__main__":
    unittest.main()
