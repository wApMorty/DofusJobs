"""Tests for the dataset-build helpers (scripts/build_dofusdb_dataset.py):
the dofus-map <-> DofusDB name bridge and the hub de-aggregation."""

from __future__ import annotations

import importlib.util
import os
import unittest

_SPEC = importlib.util.spec_from_file_location(
    "build_dofusdb_dataset",
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                 "scripts", "build_dofusdb_dataset.py"),
)
bd = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(bd)


class BridgeTest(unittest.TestCase):
    def test_wood_strip(self):
        # DofusDB "Bois de Frêne" must reach the dofus-map tree slug "frene".
        self.assertIn("frene", bd.dofusmap_keys("Bois de Frêne"))
        self.assertIn("aquajou", bd.dofusmap_keys("Bois d'Aquajou"))

    def test_fish_first_token(self):
        self.assertIn("crabe", bd.dofusmap_keys("Crabe Sourimi"))
        self.assertIn("raie", bd.dofusmap_keys("Raie Bleue"))

    def test_exact_slug_first(self):
        self.assertEqual(bd.dofusmap_keys("Blé")[0], "ble")


class RedistributeTest(unittest.TestCase):
    def test_conserves_total_and_caps(self):
        sa = [(i, 0) for i in range(10)]
        out = bd.redistribute({(0, 0): 100}, sa, 30)
        self.assertEqual(sum(out.values()), 100)        # no XP lost
        self.assertEqual(max(out.values()), 30)         # bounded by ceiling
        self.assertEqual(sum(1 for v in out.values() if v), 10)  # spread out

    def test_no_change_below_ceiling(self):
        sa = [(0, 0), (1, 0), (2, 0), (3, 0)]
        base = {(0, 0): 26, (1, 0): 17, (2, 0): 3}
        out = bd.redistribute(base, sa, 30)
        self.assertEqual({k: v for k, v in out.items() if v}, base)

    def test_overflow_conserves_total(self):
        sa = [(i, 0) for i in range(10)]            # capacity 10*30 = 300 < 1000
        out = bd.redistribute({(0, 0): 1000}, sa, 30)
        self.assertEqual(sum(out.values()), 1000)

    def test_deterministic(self):
        sa = [(i, 0) for i in range(10)]
        a = bd.redistribute({(0, 0): 100}, sa, 30)
        b = bd.redistribute({(0, 0): 100}, sa, 30)
        self.assertEqual(a, b)


if __name__ == "__main__":
    unittest.main()
