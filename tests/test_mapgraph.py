"""Tests for map-graph pathfinding and human-readable move directions."""

from __future__ import annotations

import unittest

from dofusjobs import plan_route
from dofusjobs.mapgraph import MapGraph, path_directions


def _steps(direction_strings):
    """Total number of single-screen moves a compact direction list encodes."""
    n = 0
    for d in direction_strings:
        n += int(d.split("×")[1]) if "×" in d else 1
    return n


class DirectionsTest(unittest.TestCase):
    def test_cardinal_convention(self):
        # +x → (Est), -x ← (Ouest), +y ↓ (Sud), -y ↑ (Nord).
        self.assertEqual(path_directions([(0, 0), (1, 0)]), ["→"])
        self.assertEqual(path_directions([(0, 0), (-1, 0)]), ["←"])
        self.assertEqual(path_directions([(0, 0), (0, 1)]), ["↓"])
        self.assertEqual(path_directions([(0, 0), (0, -1)]), ["↑"])

    def test_consecutive_runs_merge_but_keep_order(self):
        path = [(-1, -29), (0, -29), (1, -29), (1, -30)]
        self.assertEqual(path_directions(path), ["→×2", "↑"])
        # an interleaved path must NOT be reordered (would cross missing maps)
        path2 = [(0, 0), (1, 0), (1, 1), (2, 1)]
        self.assertEqual(path_directions(path2), ["→", "↓", "→"])

    def test_single_or_empty_path(self):
        self.assertEqual(path_directions([(0, 0)]), [])
        self.assertEqual(path_directions([]), [])


class ShortestPathTest(unittest.TestCase):
    def test_path_follows_existing_maps_only(self):
        # An L-shaped corridor: the straight diagonal map (1,0) does not exist.
        g = MapGraph([(0, 0), (0, 1), (1, 1)])
        self.assertEqual(g.shortest_path((0, 0), (1, 1)), [(0, 0), (0, 1), (1, 1)])
        self.assertEqual(g.directions((0, 0), (1, 1)), ["↓", "→"])

    def test_disconnected_is_labelled_as_jump(self):
        g = MapGraph([(0, 0), (5, 5)])
        self.assertIsNone(g.shortest_path((0, 0), (5, 5)))
        self.assertEqual(g.directions((0, 0), (5, 5)), ["saut (zaap/bateau)"])


class RouteInvariantTest(unittest.TestCase):
    def test_directions_match_travel_cost(self):
        r = plan_route(job_levels={j: 1 for j in
                       ("lumberjack", "miner", "herbalist", "farmer", "fisherman")},
                       pods_limit=300, lambda_travel=1.0)
        self.assertGreater(len(r.route), 1)
        self.assertEqual(r.route[0].directions, [])          # free start
        for stop in r.route[1:]:
            self.assertEqual(_steps(stop.directions), stop.travel_cost_from_prev)


if __name__ == "__main__":
    unittest.main()
