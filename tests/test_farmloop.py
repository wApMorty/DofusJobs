"""Tests for the v4 greedy leveling-route finder (farmloop): no pods, maps
scored by total %XP, all jobs progress, richest-component anchoring."""

from __future__ import annotations

import unittest

from dofusjobs.farmloop import FarmLoopFinder
from dofusjobs.leveling import JobXpTable
from dofusjobs.models import Cell, CellResource, Resource


def cell(cid, coords, *res):
    return Cell(cid, coords, tuple(CellResource(r, q) for r, q in res))


def grid(cells, extra=()):
    """A gap-free rectangle of maps covering the cell coords (so BFS works),
    plus any explicit ``extra`` coords (e.g. a separate island)."""
    xs = [c.world_coords[0] for c in cells]
    ys = [c.world_coords[1] for c in cells]
    maps = [(x, y) for x in range(min(xs) - 1, max(xs) + 2)
            for y in range(min(ys) - 1, max(ys) + 2)]
    return maps + list(extra)


def finder(resources, cells, maps=None):
    rmap = {r.resource_id: r for r in resources}
    return FarmLoopFinder(rmap, cells, maps=maps or grid(cells), xp_table=JobXpTable.load())


class FarmLoopTest(unittest.TestCase):
    def test_heavy_resource_is_not_penalised(self):
        # A very heavy resource (100 pods) must still be harvested — pods are gone.
        heavy = Resource("ore", "Ore", "miner", 30, 1, 1, 100)
        cells = [cell("a", (0, 0), ("ore", 5))]
        r = finder([heavy], cells).find({"miner": 1})
        used = {h["resource_id"] for s in r.stops for h in s.harvests}
        self.assertIn("ore", used)
        self.assertGreater(r.end_levels["miner"], 1)

    def test_all_eligible_jobs_progress(self):
        res = [Resource("w", "W", "lumberjack", 20, 1, 1, 5),
               Resource("o", "O", "miner", 20, 1, 1, 5),
               Resource("h", "H", "herbalist", 20, 1, 1, 5)]
        cells = [cell("a", (0, 0), ("w", 10)),
                 cell("b", (0, 1), ("o", 10)),
                 cell("c", (1, 0), ("h", 10))]
        r = finder(res, cells).find({"lumberjack": 1, "miner": 1, "herbalist": 1})
        self.assertEqual(set(r.per_job), {"lumberjack", "miner", "herbalist"})
        self.assertTrue(all(v > 0 for v in r.per_job.values()))

    def test_starts_in_richest_component(self):
        poor = Resource("p", "P", "miner", 10, 1, 1, 1)
        rich = Resource("r", "R", "miner", 10, 1, 1, 1)
        cells = [cell("isle", (0, 0), ("p", 1)),
                 cell("main", (50, 0), ("r", 80))]
        maps = [(0, y) for y in (-1, 0, 1)] + [(50, y) for y in (-1, 0, 1)]
        r = finder([poor, rich], cells, maps=maps).find({"miner": 1})
        used = {h["resource_id"] for s in r.stops for h in s.harvests}
        self.assertEqual(used, {"r"})

    def test_no_eligible_spot_when_all_gated(self):
        gated = Resource("hi", "Hi", "miner", 30, 200, 200, 1)
        r = finder([gated], [cell("a", (0, 0), ("hi", 5))]).find({"miner": 1})
        self.assertEqual(r.stops, [])
        self.assertEqual(r.terminated, "no_eligible_spot")

    def test_deterministic(self):
        res = [Resource("w", "W", "lumberjack", 20, 1, 1, 5)]
        cells = [cell(f"c{i}", (i, 0), ("w", 5)) for i in range(4)]
        f = finder(res, cells)
        a = f.find({"lumberjack": 1})
        b = f.find({"lumberjack": 1})
        self.assertEqual([s.world_coords for s in a.stops],
                         [s.world_coords for s in b.stops])


if __name__ == "__main__":
    unittest.main()
