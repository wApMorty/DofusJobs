"""Tests for the per-job selection feature (checkboxes -> active_jobs).

Covers: the route ignoring deselected jobs (no harvests, no level-ups), the
active_jobs=None / full-set backward-compatibility no-op, the empty-set => empty
route case, the harvest_coord/advance banking gate (an ignored job never gains XP
even on a free transit pickup), and the web /api/plan plumbing of active_jobs.
"""

from __future__ import annotations

import unittest

from dofusjobs.farmloop import FarmLoopFinder
from dofusjobs.leveling import JobXpTable
from dofusjobs.models import GATHERING_JOBS, Cell, CellResource, Resource


def cell(cid, coords, *res):
    return Cell(cid, coords, tuple(CellResource(r, q) for r, q in res))


def grid(cells, extra=()):
    xs = [c.world_coords[0] for c in cells]
    ys = [c.world_coords[1] for c in cells]
    maps = [(x, y) for x in range(min(xs) - 1, max(xs) + 2)
            for y in range(min(ys) - 1, max(ys) + 2)]
    return maps + list(extra)


def finder(resources, cells, maps=None):
    rmap = {r.resource_id: r for r in resources}
    return FarmLoopFinder(rmap, cells, maps=maps or grid(cells), xp_table=JobXpTable.load())


def levels1():
    return {j: 1 for j in GATHERING_JOBS}


def xp1(f):
    return {j: f.xp_table.xp_for_level(1) for j in GATHERING_JOBS}


def two_jobs():
    # Each map bears both a lumberjack and a miner resource, so the route would
    # naturally level both unless a job is deselected.
    res = [Resource("w", "Wood", "lumberjack", 20, 1),
           Resource("o", "Ore", "miner", 20, 1)]
    cells = [cell(f"c{i}", (i, 0), ("w", 5), ("o", 5)) for i in range(5)]
    return finder(res, cells)


class SubsetTest(unittest.TestCase):
    def test_route_only_touches_selected_jobs(self):
        f = two_jobs()
        r = f.find(levels1(), lambda_travel=0.5, active_jobs={"lumberjack"})
        used = {h["job_id"] for s in r.stops for h in s.harvests}
        self.assertEqual(used, {"lumberjack"})
        # only the selected job rose above its starting level
        self.assertGreater(r.end_levels["lumberjack"], 1)
        self.assertEqual(r.end_levels["miner"], 1)
        self.assertNotIn("miner", r.per_job)

    def test_plan_window_only_touches_selected_jobs(self):
        f = two_jobs()
        win = f.plan_window(None, xp1(f), [], horizon=4, lambda_travel=0.5,
                            active_jobs={"miner"})
        used = {h["job_id"] for s in win for h in s["harvests"]}
        self.assertEqual(used, {"miner"})


class NoneEquivalenceTest(unittest.TestCase):
    def test_full_set_equals_none(self):
        f = two_jobs()
        base = [s.world_coords for s in f.find(levels1(), lambda_travel=0.5).stops]
        for aj in (None, set(GATHERING_JOBS), {"lumberjack", "miner"}):
            got = [s.world_coords for s in
                   f.find(levels1(), lambda_travel=0.5, active_jobs=aj).stops]
            self.assertEqual(got, base)
        # harvests are identical too (both jobs still banked)
        base_h = {h["job_id"] for s in f.find(levels1(), lambda_travel=0.5).stops
                  for h in s.harvests}
        self.assertEqual(base_h, {"lumberjack", "miner"})


class EmptySetTest(unittest.TestCase):
    def test_empty_set_yields_empty_route(self):
        f = two_jobs()
        r = f.find(levels1(), lambda_travel=0.5, active_jobs=set())
        self.assertEqual(r.stops, [])
        self.assertEqual(r.start_levels, r.end_levels)   # nothing leveled
        self.assertEqual(r.per_job, {})

    def test_empty_set_yields_empty_window(self):
        f = two_jobs()
        self.assertEqual(f.plan_window(None, xp1(f), [], horizon=4, active_jobs=set()), [])


class BankingGateTest(unittest.TestCase):
    def test_harvest_coord_skips_deselected_job(self):
        f = two_jobs()
        jx = xp1(f)
        nx = f.harvest_coord(jx, (0, 0), active_jobs={"lumberjack"})
        self.assertGreater(nx["lumberjack"], jx["lumberjack"])
        self.assertEqual(nx["miner"], jx["miner"])      # deselected job never banked

    def test_advance_is_noop_for_deselected_job(self):
        f = two_jobs()
        jx = xp1(f)
        # a map bearing only a miner resource, advanced while only lumberjack selected
        res = [Resource("o", "Ore", "miner", 20, 1)]
        f2 = finder(res, [cell("m", (0, 0), ("o", 5))])
        jx2 = xp1(f2)
        njx, vis = f2.advance(jx2, [], (0, 0), active_jobs={"lumberjack"})
        self.assertEqual(njx["miner"], jx2["miner"])    # ignored job gained zero xp
        self.assertIn([0, 0], vis)


class ApiPlumbingTest(unittest.TestCase):
    """The web /api/plan reads active_jobs from the payload and restricts the
    route, with an absent key meaning back-compat None (a tiny finder is injected)."""

    def setUp(self):
        import webapp.app as appmod
        self.app = appmod
        res = [Resource("w", "Wood", "lumberjack", 20, 1),
               Resource("o", "Ore", "miner", 20, 1)]
        cells = [cell(f"c{i}", (i, 0), ("w", 5), ("o", 5)) for i in range(4)]
        self.f = finder(res, cells)
        self._saved = appmod._FINDER["obj"]
        appmod._FINDER["obj"] = self.f

    def tearDown(self):
        self.app._FINDER["obj"] = self._saved

    def _plan(self, **extra):
        return self.app.plan({"engine": "beam", "metric": "levels",
                              "job_levels": {j: 1 for j in GATHERING_JOBS}, **extra})

    def test_selected_jobs_restrict_the_window(self):
        d = self._plan(active_jobs=["lumberjack"])
        used = {h["job_id"] for s in d["window"] for h in s["harvests"]}
        self.assertEqual(used, {"lumberjack"})

    def test_absent_key_is_back_compat(self):
        d = self._plan()
        used = {h["job_id"] for s in d["window"] for h in s["harvests"]}
        self.assertEqual(used, {"lumberjack", "miner"})

    def test_empty_list_yields_no_route(self):
        d = self._plan(active_jobs=[])
        self.assertEqual(d["window"], [])
        self.assertTrue(d["done"])


if __name__ == "__main__":
    unittest.main()
