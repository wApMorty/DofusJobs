"""Tests for the MCTS/UCT rolling planner (``plan_window_mcts``): determinism,
the same window contract as the beam, the explicit-crossed-map semantics, and a
guard that MCTS is never worse than the beam on a fixed scenario."""

from __future__ import annotations

import unittest

from dofusjobs.models import GATHERING_JOBS, Resource

# Reuse the small-graph builders the beam tests already use.
from tests.test_farmloop import cell, finder


def _xp(f, level=1):
    return {j: f.xp_table.xp_for_level(level) for j in GATHERING_JOBS}


def _roll_rate(f, plan, job_xp, k=12):
    """Drive ``plan(pos, jx, vis) -> window`` through ``k`` committed stops and
    return (total_value, screens, rate) — the same rolling loop the UI runs."""
    jx = dict(job_xp)
    vis: list = []
    pos = None
    value = 0.0
    screens = 0
    for _ in range(k):
        window = plan(pos, jx, vis)
        if not window:
            break
        first = window[0]
        value += first["value"]
        screens += first.get("travel", 0)
        jx, vis = f.advance(jx, vis, first["world_coords"])
        pos = first["world_coords"]
    return value, screens, (value / screens if screens else value)


class MctsContractTest(unittest.TestCase):
    def _finder(self):
        res = [Resource("w", "W", "lumberjack", 20, 1)]
        cells = [cell(f"c{i}", (i, 0), ("w", 5)) for i in range(6)]
        return finder(res, cells)

    def test_deterministic(self):
        f = self._finder()
        a = f.plan_window_mcts(None, _xp(f), [], horizon=4, lambda_travel=1.0,
                               simulations=200)
        b = f.plan_window_mcts(None, _xp(f), [], horizon=4, lambda_travel=1.0,
                               simulations=200)
        self.assertEqual([s["world_coords"] for s in a],
                         [s["world_coords"] for s in b])

    def test_window_contract(self):
        f = self._finder()
        w = f.plan_window_mcts(None, _xp(f), [], horizon=4, lambda_travel=0.0,
                               simulations=200)
        self.assertTrue(w)
        self.assertLessEqual(len(w), 4)
        for s in w:
            self.assertEqual(set(s), {"world_coords", "directions", "harvests",
                                      "value", "travel"})
        coords = [tuple(s["world_coords"]) for s in w]
        self.assertEqual(len(set(coords)), len(coords))   # all distinct

    def test_respects_visited(self):
        f = self._finder()
        w0 = f.plan_window_mcts(None, _xp(f), [], horizon=2, lambda_travel=0.0,
                                simulations=150)
        first = w0[0]["world_coords"]
        w1 = f.plan_window_mcts(None, _xp(f), [first], horizon=2, lambda_travel=0.0,
                                simulations=150)
        self.assertNotIn(tuple(first), [tuple(s["world_coords"]) for s in w1])

    def test_inlines_crossed_maps_as_steps(self):
        # A small resource map between two rich endpoints must surface as its own
        # explicit step (same flattening as the beam), not a silent pick-up.
        a = Resource("a", "A", "miner", 20, 1)
        cells = [cell("s", (0, 0), ("a", 10)), cell("e", (5, 0), ("a", 10)),
                 cell("m", (2, 0), ("a", 3))]
        f = finder([a], cells)
        jx = {j: f.xp_table.xp_for_level(1) for j in GATHERING_JOBS}
        w = f.plan_window_mcts((0, 0), jx, [[0, 0]], horizon=6, lambda_travel=0.0,
                               simulations=200)
        coords = [tuple(s["world_coords"]) for s in w]
        self.assertIn((2, 0), coords)


class MctsQualityTest(unittest.TestCase):
    def test_no_eligible_returns_empty(self):
        gated = Resource("hi", "Hi", "miner", 30, 200)
        f = finder([gated], [cell("a", (0, 0), ("hi", 5))])
        w = f.plan_window_mcts(None, {j: f.xp_table.xp_for_level(1) for j in GATHERING_JOBS},
                               [], horizon=5, simulations=100)
        self.assertEqual(w, [])

    def test_not_worse_than_beam(self):
        # On a fixed 2-D field with travel costs, MCTS (lookahead by simulation)
        # must be at least as fast (value per screen) as the beam.
        res = [Resource("w", "W", "lumberjack", 20, 1),
               Resource("o", "O", "miner", 18, 1)]
        cells = [cell(f"c{x}_{y}", (x, y), ("w", 6) if (x + y) % 2 == 0 else ("o", 6))
                 for x in range(5) for y in range(3)]
        f = finder(res, cells)
        jx = {j: f.xp_table.xp_for_level(1) for j in GATHERING_JOBS}

        def beam(pos, j, v):
            return f.plan_window(pos, j, v, horizon=8, lambda_travel=1.0)

        def mcts(pos, j, v):
            return f.plan_window_mcts(pos, j, v, horizon=8, lambda_travel=1.0,
                                      simulations=400)

        _bv, _bs, beam_rate = _roll_rate(f, beam, jx, k=10)
        _mv, _ms, mcts_rate = _roll_rate(f, mcts, jx, k=10)
        self.assertGreaterEqual(mcts_rate, beam_rate * 0.999)


if __name__ == "__main__":
    unittest.main()
