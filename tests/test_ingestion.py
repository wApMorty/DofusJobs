"""Tests for the real-data ingestion layer (DofusDB-built dataset)."""

from __future__ import annotations

import unittest

from dofusjobs.ingestion import load_cells, load_dataset, load_maps, load_resources
from dofusjobs.models import GATHERING_JOBS


class RealDatasetTest(unittest.TestCase):
    def test_dataset_loads(self):
        resources, cells, maps = load_dataset()
        self.assertGreater(len(resources), 50)   # ~85 gathering resources
        self.assertGreater(len(cells), 1000)     # thousands of real cells
        self.assertGreater(len(maps), 1000)       # thousands of real maps

    def test_resources_satisfy_contract(self):
        for r in load_resources().values():
            self.assertIn(r.job_id, GATHERING_JOBS)
            self.assertGreaterEqual(r.base_xp, 1)
            self.assertGreaterEqual(r.required_level, 1)
            self.assertGreaterEqual(r.pods, 1)

    def test_all_five_jobs_present(self):
        jobs = {r.job_id for r in load_resources().values()}
        self.assertEqual(jobs, set(GATHERING_JOBS))

    def test_cells_reference_known_resources(self):
        resources = load_resources()
        cells = load_cells(resources)
        self.assertGreater(len(cells), 0)
        for c in cells[:200]:
            self.assertTrue(c.resources)
            for cr in c.resources:
                self.assertIn(cr.resource_id, resources)
                self.assertGreater(cr.quantity, 0)

    def test_maps_are_coordinate_pairs(self):
        maps = load_maps()
        for m in maps[:50]:
            self.assertEqual(len(m), 2)
            self.assertIsInstance(m[0], int)


if __name__ == "__main__":
    unittest.main()
