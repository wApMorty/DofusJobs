"""Tests for the v2 optimizer: pods feasibility, gating, free start, the
weighted objective, in-plan level-up simulation (mid-run unlocks), and
determinism."""

from __future__ import annotations

import unittest

from dofusjobs.leveling import JobXpTable
from dofusjobs.models import (
    Cell,
    CellResource,
    GATHERING_JOBS,
    PlayerInput,
    Resource,
)
from dofusjobs.optimizer import Optimizer


def table_with(thresholds):
    """Tiny XP table: dict level->cumulative xp; fills 1..200 monotonically."""
    t = JobXpTable._interpolate({1: 0, **thresholds, 200: max(thresholds.values()) + 1})
    return JobXpTable(t)


def cell(cid, coords, *res):  # res: (resource_id, qty)
    return Cell(cid, coords, tuple(CellResource(r, q) for r, q in res))


def opt(resources, cells, table=None):
    rmap = {r.resource_id: r for r in resources}
    # A connected rectangular grid covering the cell coords so BFS travel works
    # (mirrors a gap-free local region; distance == Manhattan here).
    xs = [c.world_coords[0] for c in cells]
    ys = [c.world_coords[1] for c in cells]
    maps = [(x, y) for x in range(min(xs) - 1, max(xs) + 2)
            for y in range(min(ys) - 1, max(ys) + 2)]
    return Optimizer(rmap, cells, maps=maps, xp_table=table or JobXpTable.load())


def xp_for(opt_, levels):
    return {j: opt_.xp_table.xp_for_level(levels.get(j, 1)) for j in GATHERING_JOBS}


class PodsFeasibilityTest(unittest.TestCase):
    def test_never_exceeds_limit(self):
        r = Resource("a", "A", "miner", 10, 1, 1, 7)
        cells = [cell(f"c{i}", (5 + i, -18), ("a", 50)) for i in range(4)]
        o = opt([r], cells)
        for limit in (1, 6, 7, 13, 100, 101):
            res = o.optimize(PlayerInput(xp_for(o, {"miner": 50}), limit, 0.0))
            self.assertLessEqual(res.pods_used, limit, f"limit={limit}")
            self.assertGreaterEqual(res.pods_used, 0)

    def test_item_heavier_than_limit_harvests_nothing(self):
        r = Resource("h", "Heavy", "fisherman", 50, 1, 1, 20)
        o = opt([r], [cell("c", (5, -18), ("h", 10))])
        res = o.optimize(PlayerInput(xp_for(o, {"fisherman": 50}), 10, 0.0))
        self.assertEqual(res.total_xp, 0)
        self.assertEqual(res.terminated_reason, "pods_full")


class GatingTest(unittest.TestCase):
    def test_required_level_blocks_then_no_eligible(self):
        high = Resource("high", "High", "farmer", 99, 60, 60, 2)
        o = opt([high], [cell("c", (6, -19), ("high", 100))])
        res = o.optimize(PlayerInput(xp_for(o, {"farmer": 10}), 20, 0.0))
        self.assertEqual(res.total_xp, 0)
        self.assertEqual(res.terminated_reason, "no_eligible_spot")


class FreeStartTest(unittest.TestCase):
    def test_first_stop_has_zero_travel_and_no_astrub_anchor(self):
        r = Resource("r", "R", "herbalist", 30, 1, 1, 3)
        # Only cell is far from the old Astrub [4,-19]; free start => travel 0.
        o = opt([r], [cell("far", (40, 30), ("r", 50))])
        res = o.optimize(PlayerInput(xp_for(o, {"herbalist": 50}), 30, 5.0))
        self.assertEqual(res.route[0].world_coords, (40, 30))
        self.assertEqual(res.route[0].travel_cost_from_prev, 0)
        self.assertGreater(res.total_xp, 0)


class WeightedObjectiveTest(unittest.TestCase):
    def _o(self):
        near = Resource("near", "Near", "miner", 20, 1, 1, 5)
        far = Resource("far", "Far", "miner", 21, 1, 1, 5)
        cells = [cell("n", (5, -19), ("near", 100)), cell("f", (45, -19), ("far", 100))]
        return opt([near, far], cells)

    def test_high_lambda_avoids_marginal_far_detour(self):
        o = self._o()
        res = o.optimize(PlayerInput(xp_for(o, {"miner": 50}), 50, 5.0))
        used = {h.resource_id for s in res.route for h in s.harvests}
        # Starts free at one cell; the ~40-screen hop for +1 xp/item must be skipped.
        self.assertEqual(len(res.route), 1)
        self.assertTrue(used <= {"near"} or used <= {"far"})

    def test_levels_metric_does_not_stall_at_high_level(self):
        # Regression: with metric='levels', lambda is XP-per-screen and must be
        # converted to %-of-a-level at the current level. Otherwise a raw lambda
        # crushes the (tiny, ~1/level) % gains at high level and the route stalls
        # after the free first cell with pods to spare.
        tbl = table_with({200: 400000})           # expensive levels: ~2010 xp/level
        r = Resource("ore", "Ore", "miner", 30, 1, 1, 1)   # 1 unit/cell, 1 pod
        cells = [cell(f"c{y}", (0, y), ("ore", 1)) for y in range(5)]  # a 5-screen line
        o = opt([r], cells, table=tbl)
        res = o.optimize(PlayerInput(xp_for(o, {"miner": 150}), pods_limit=100,
                                     lambda_travel=2.0, metric="levels"))
        # A neighbour 1 screen away (+30 xp) is worth far more than lambda*1 xp,
        # so the route must walk the whole line, not stop at the start cell.
        self.assertEqual(res.pods_used, 5)
        self.assertGreater(len(res.route), 1)


class LevelUpSimulationTest(unittest.TestCase):
    def test_mid_run_unlock_higher_tier(self):
        # Cheap table: 200 xp -> level 10. Low tier req1, high tier req10, same cell.
        tbl = table_with({10: 200})
        low = Resource("low", "Low", "lumberjack", 20, 1, 1, 5)    # 4.0 xp/pod
        high = Resource("high", "High", "lumberjack", 60, 10, 10, 5)  # 12 xp/pod, gated
        c = cell("forest", (5, -18), ("low", 40), ("high", 40))
        o = opt([low, high], [c], table=tbl)
        # Start level 1 (0 xp): only 'low' eligible. Harvest enough 'low' to pass
        # 200 xp (=10 harvests) -> level 10 -> 'high' unlocks and gets harvested.
        # (metric=xp: the unlock mechanism is independent of the objective metric.)
        res = o.optimize(PlayerInput({j: 0 for j in GATHERING_JOBS}, pods_limit=300,
                                     lambda_travel=0.1, metric="xp"))
        used = {h.resource_id for s in res.route for h in s.harvests}
        self.assertIn("low", used)
        self.assertIn("high", used, "high tier should unlock mid-pass")
        self.assertTrue(any(lu.unlocked for lu in res.level_ups))
        self.assertEqual(res.end_levels["lumberjack"],
                         max(lu.to_level for lu in res.level_ups))

    def test_no_levelup_when_already_high(self):
        # Real table: level 50 ~ 58k xp; a few harvests (100 xp) cannot cross a tier.
        low = Resource("low", "Low", "lumberjack", 20, 1, 1, 5)
        o = opt([low], [cell("forest", (5, -18), ("low", 5))])
        res = o.optimize(PlayerInput(xp_for(o, {"lumberjack": 50}), 100, 0.1))
        self.assertEqual(res.level_ups, [])


class DeterminismTest(unittest.TestCase):
    def test_repeatable(self):
        r = Resource("r", "R", "herbalist", 10, 1, 1, 2)
        cells = [cell(f"c{i}", (5 + (i % 4), -18 - i // 4), ("r", 10)) for i in range(6)]
        o = opt([r], cells)
        runs = [o.optimize(PlayerInput(xp_for(o, {"herbalist": 50}), 30, 1.0)) for _ in range(3)]
        seqs = [[s.cell_id for s in r.route] for r in runs]
        self.assertEqual(seqs[0], seqs[1])
        self.assertEqual(seqs[1], seqs[2])


class LevelProgressTest(unittest.TestCase):
    def test_continuous_level(self):
        t = JobXpTable.load()
        self.assertAlmostEqual(t.level_progress(t.xp_for_level(10)), 10.0, places=6)
        band = t.xp_for_level(11) - t.xp_for_level(10)
        mid = t.level_progress(t.xp_for_level(10) + band // 2)  # ~10.5
        self.assertTrue(10.0 < mid < 11.0)
        self.assertEqual(t.level_progress(0), 1.0)


class MetricBalancingTest(unittest.TestCase):
    """The '% of level' metric must favor a lagging low-level job over pouring
    XP into an already-advanced job (the user's core request)."""

    def _setup(self):
        ore = Resource("ore", "Ore", "miner", 20, 1, 1, 5)        # low job, low xp
        wood = Resource("wood", "Wood", "lumberjack", 40, 1, 1, 5)  # high job, high xp
        cells = [cell("c", (5, -18), ("ore", 100), ("wood", 100))]
        return opt([ore, wood], cells)

    def _player(self, o, metric):
        xp = {j: 0 for j in GATHERING_JOBS}
        xp["lumberjack"] = o.xp_table.xp_for_level(60)  # already advanced
        return PlayerInput(xp, pods_limit=100, lambda_travel=0.0, metric=metric)

    def test_xp_metric_pours_into_advanced_job(self):
        o = self._setup()
        res = o.optimize(self._player(o, "xp"))
        self.assertGreater(res.xp_by_job.get("lumberjack", 0),
                           res.xp_by_job.get("miner", 0))

    def test_levels_metric_favors_lagging_job(self):
        o = self._setup()
        res = o.optimize(self._player(o, "levels"))
        self.assertGreater(res.levels_gained.get("miner", 0),
                           res.levels_gained.get("lumberjack", 0))
        # And it makes more total level progress than the raw-XP plan.
        xp_res = o.optimize(self._player(o, "xp"))
        self.assertGreater(res.total_levels_gained, xp_res.total_levels_gained)


class CellAnalysisTest(unittest.TestCase):
    def test_available_xp_respects_levels(self):
        low = Resource("low", "Low", "miner", 10, 1, 1, 2)
        high = Resource("high", "High", "miner", 50, 30, 30, 2)
        o = opt([low, high], [cell("mine", (6, -20), ("low", 10), ("high", 10))])
        a1 = o.cell_analysis({"miner": 1})[0]
        a2 = o.cell_analysis({"miner": 40})[0]
        self.assertEqual(a1["available_xp"], 100)        # only low
        self.assertEqual(a2["available_xp"], 100 + 500)  # low + high


if __name__ == "__main__":
    unittest.main()
