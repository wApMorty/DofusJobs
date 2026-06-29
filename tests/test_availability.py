"""Tests for the adaptive map-availability model (anti bot-depletion).

Covers: the EWMA math (decay / recovery / convergence to the true fill rate),
the availability discount inside the shared _cell_value scoring path, discounted
free-start anchoring, the free on-the-way pickup of a low-a map, the all-1
backward-compatibility no-op, determinism, the three /api/plan commit kinds, and
a deterministic seeded instance of the depletion simulation.
"""

from __future__ import annotations

import importlib.util
import os
import unittest

from dofusjobs.farmloop import AVAILABILITY_ALPHA, FarmLoopFinder, ewma_update
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


class EwmaTest(unittest.TestCase):
    def test_alpha_value(self):
        self.assertEqual(AVAILABILITY_ALPHA, 0.20)

    def test_decay_on_empties(self):
        # alpha=0.20 => each "empty" multiplies availability by 0.8 (obs=0).
        a = 1.0
        for expected in (0.80, 0.64, 0.512):
            a = ewma_update(a, 0.0)
            self.assertAlmostEqual(a, expected, places=6)

    def test_recovery_on_harvest(self):
        # a harvest (obs=1) pulls availability back up by 0.8*a + 0.2.
        self.assertAlmostEqual(ewma_update(0.5, 1.0), 0.6, places=6)
        self.assertAlmostEqual(ewma_update(1.0, 1.0), 1.0, places=6)  # stays at the cap

    def test_stays_in_unit_interval(self):
        a = 1.0
        for obs in [0, 0, 0, 1, 0, 1, 1, 0] * 5:
            a = ewma_update(a, float(obs))
            self.assertGreater(a, 0.0)
            self.assertLessEqual(a, 1.0)

    def _tail_mean(self, pattern, cycles=200, tail=50):
        # Drive the EWMA with a repeating 0/1 block and return the mean availability
        # over the last ``tail`` samples (its converged level = the block's mean).
        a = 1.0
        seen = []
        for _ in range(cycles):
            for obs in pattern:
                a = ewma_update(a, float(obs))
                seen.append(a)
        return sum(seen[-tail:]) / tail

    def test_converges_to_true_fill_rate(self):
        # The converged availability equals the observation's empirical fill rate,
        # for both a symmetric and an asymmetric pattern (the "representative value"
        # claim, not just the pure-empty 0.8^k and single-harvest 0.8a+0.2 cases).
        self.assertAlmostEqual(self._tail_mean([1, 0]), 0.5, delta=0.02)        # p=0.50
        self.assertAlmostEqual(self._tail_mean([1, 0, 0, 0]), 0.25, delta=0.02)  # p=0.25
        self.assertAlmostEqual(self._tail_mean([1, 1, 1, 0]), 0.75, delta=0.02)  # p=0.75


class DiscountTest(unittest.TestCase):
    def _cellval(self, f, avail):
        return f._cell_value(f.cells[0], levels1(), xp1(f), "levels", availability=avail)[0]

    def test_value_scaled_by_availability(self):
        f = finder([Resource("w", "W", "lumberjack", 20, 1)],
                   [cell("a", (0, 0), ("w", 10))])
        full = self._cellval(f, None)
        self.assertGreater(full, 0)
        self.assertAlmostEqual(self._cellval(f, {(0, 0): 0.5}), full * 0.5, places=9)
        self.assertAlmostEqual(self._cellval(f, {(0, 0): 0.8 ** 3}), full * 0.512, places=9)
        # an absent coord keeps a=1.0 (no discount)
        self.assertAlmostEqual(self._cellval(f, {(9, 9): 0.1}), full, places=9)

    def test_anchor_follows_discounted_value(self):
        # Two islands: a rich one and a poor one. Undiscounted the free start
        # anchors in the rich island; decaying the rich map flips the anchor.
        poor = Resource("p", "P", "miner", 10, 1)
        rich = Resource("r", "R", "miner", 10, 1)
        cells = [cell("isle", (0, 0), ("p", 5)), cell("main", (50, 0), ("r", 80))]
        maps = [(0, y) for y in (-1, 0, 1)] + [(50, y) for y in (-1, 0, 1)]
        f = finder([poor, rich], cells, maps=maps)
        used0 = {h["resource_id"] for s in f.find(levels1()).stops for h in s.harvests}
        self.assertEqual(used0, {"r"})
        decayed = f.find(levels1(), availability={(50, 0): 0.01})
        used1 = {h["resource_id"] for s in decayed.stops for h in s.harvests}
        self.assertEqual(used1, {"p"})


class FreePickupTest(unittest.TestCase):
    def test_low_a_map_on_path_is_still_harvested(self):
        # (1,0) has a near-zero availability so it is never a DETOUR target, but it
        # lies on the BFS path (2,0)->(0,0); the travel penalty is not discounted,
        # so it is still harvested as a free on-the-way pickup.
        res = [Resource("w", "W", "lumberjack", 20, 1)]
        cells = [cell("c0", (0, 0), ("w", 5)), cell("c1", (1, 0), ("w", 5)),
                 cell("c2", (2, 0), ("w", 5))]
        f = finder(res, cells)
        avail = {(1, 0): 0.001}
        r = f.find(levels1(), lambda_travel=0.5, availability=avail)
        by_coord = {s.world_coords: s for s in r.stops if s.harvests}
        self.assertIn((1, 0), by_coord)
        # the XP actually banked at the low-a transit map is the FULL base_xp x
        # quantity (availability discounts the decision value, never realized XP).
        h = by_coord[(1, 0)].harvests[0]
        self.assertEqual((h["xp"], h["quantity"]), (20, 5))
        # planning must not mutate the caller's availability dict (it is pure).
        self.assertEqual(avail, {(1, 0): 0.001})

    def test_planning_does_not_mutate_availability(self):
        res = [Resource("w", "W", "lumberjack", 20, 1)]
        cells = [cell(f"c{i}", (i, 0), ("w", 5)) for i in range(4)]
        f = finder(res, cells)
        avail = {(1, 0): 0.3, (2, 0): 0.7}
        f.find(levels1(), lambda_travel=0.5, availability=avail)
        f.plan_window(None, xp1(f), [], horizon=4, lambda_travel=0.5, availability=avail)
        f.plan_window_mcts(None, xp1(f), [], horizon=4, lambda_travel=0.5, availability=avail)
        self.assertEqual(avail, {(1, 0): 0.3, (2, 0): 0.7})


class BackwardCompatTest(unittest.TestCase):
    def _f(self):
        res = [Resource("w", "W", "lumberjack", 20, 1)]
        cells = [cell(f"c{i}", (i, 0), ("w", 5)) for i in range(5)]
        return finder(res, cells)

    def test_find_all_one_is_noop(self):
        f = self._f()
        base = [s.world_coords for s in f.find(levels1(), lambda_travel=0.5).stops]
        for avail in (None, {}, {(0, 0): 1.0, (3, 0): 1.0}):
            got = [s.world_coords for s in
                   f.find(levels1(), lambda_travel=0.5, availability=avail).stops]
            self.assertEqual(got, base)

    def test_plan_window_all_one_is_noop(self):
        f = self._f()
        base = f.plan_window(None, xp1(f), [], horizon=4, lambda_travel=0.5)
        for avail in (None, {}, {(2, 0): 1.0}):
            got = f.plan_window(None, xp1(f), [], horizon=4, lambda_travel=0.5,
                                availability=avail)
            self.assertEqual([s["world_coords"] for s in got],
                             [s["world_coords"] for s in base])

    def test_deterministic_with_availability(self):
        f = self._f()
        kw = dict(lambda_travel=0.5, availability={(1, 0): 0.3, (3, 0): 0.7})
        a = [s.world_coords for s in f.find(levels1(), **kw).stops]
        b = [s.world_coords for s in f.find(levels1(), **kw).stops]
        self.assertEqual(a, b)


class ApiCommitKindsTest(unittest.TestCase):
    """The three /api/plan commit kinds drive the availability EWMA correctly,
    without touching the real dataset (a tiny finder is injected into the app)."""

    def setUp(self):
        import webapp.app as appmod
        self.app = appmod
        res = [Resource("w", "W", "lumberjack", 20, 1)]
        cells = [cell(f"c{i}", (i, 0), ("w", 5)) for i in range(3)]
        self.f = finder(res, cells)
        self._saved = appmod._FINDER["obj"]
        appmod._FINDER["obj"] = self.f

    def tearDown(self):
        self.app._FINDER["obj"] = self._saved

    def _state(self):
        jx = {j: self.app._XP_TABLE.xp_for_level(1) for j in GATHERING_JOBS}
        return {"pos": None, "job_xp": jx, "visited": [], "availability": {"2,0": 0.5}}

    def _commit(self, commit):
        d = self.app.plan({"engine": "beam", "metric": "levels",
                           "state": self._state(), "commit": commit})
        return d["state"]

    def test_empty_decays_and_marks_visited_without_advancing(self):
        st = self._commit({"coord": [2, 0], "kind": "empty"})
        self.assertAlmostEqual(st["availability"]["2,0"], 0.4, places=6)  # 0.8*0.5
        self.assertIn([2, 0], st["visited"])
        self.assertIsNone(st["pos"])                                       # did NOT advance

    def test_advance_recovers_and_moves(self):
        st = self._commit({"coord": [2, 0], "kind": "advance"})
        self.assertAlmostEqual(st["availability"]["2,0"], 0.6, places=6)  # 0.8*0.5+0.2
        self.assertEqual(st["pos"], [2, 0])

    def test_skip_leaves_availability_unchanged(self):
        st = self._commit({"coord": [2, 0], "kind": "skip"})
        self.assertAlmostEqual(st["availability"]["2,0"], 0.5, places=6)
        self.assertIn([2, 0], st["visited"])

    def test_harvest_bool_back_compat(self):
        # legacy clients post commit.harvest (bool) instead of kind
        st_adv = self._commit({"coord": [2, 0], "harvest": True})
        self.assertAlmostEqual(st_adv["availability"]["2,0"], 0.6, places=6)
        st_skip = self._commit({"coord": [2, 0], "harvest": False})
        self.assertAlmostEqual(st_skip["availability"]["2,0"], 0.5, places=6)


class DepletionSimTest(unittest.TestCase):
    def test_adaptive_beats_baseline_on_seeded_instance(self):
        path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "scripts", "sim_depletion.py")
        spec = importlib.util.spec_from_file_location("sim_depletion", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        a_rate, b_rate, _detail = mod.run(seed=0, laps=10)
        self.assertGreater(b_rate, 0.0)
        self.assertGreaterEqual(a_rate, b_rate * 1.05)


if __name__ == "__main__":
    unittest.main()
