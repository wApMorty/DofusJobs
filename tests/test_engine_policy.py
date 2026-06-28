"""Tests for the engine=auto policy resolver (dofusjobs/engine_policy.py).

The resolver maps (job levels, metric) -> (engine, lambda) via a coarse lookup
table. These tests pin the bucketisation, the deterministic lookup, the safe
fallbacks (missing file/key, all-200), the shape of the committed table, and the
web wiring of engine="auto".
"""

import json
import os
import unittest

from dofusjobs import GATHERING_JOBS, resolve_engine
from dofusjobs.engine_policy import (
    _BUILTIN_MIN_BUCKETS,
    _BUILTIN_SPREAD_BUCKETS,
    bucket_for,
    load_policy,
)

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_POLICY_PATH = os.path.join(_ROOT, "data", "engine_policy.json")

# A small injected policy so the contract tests don't depend on the generated
# (and dataset-dependent) data/engine_policy.json.
FAKE = {
    "schema": 1,
    "min_level_buckets": _BUILTIN_MIN_BUCKETS,
    "spread_buckets": _BUILTIN_SPREAD_BUCKETS,
    "default": {"engine": "beam", "lambda_travel": 1.0},
    "table": {
        "levels|low|large": {"engine": "mcts", "lambda_travel": 1.0},
        "xp|mid|flat": {"engine": "beam", "lambda_travel": 2.0},
    },
}


def _levels(min_level, spread=0):
    """Five jobs: all at ``min_level``, the last raised by ``spread``."""
    jobs = list(GATHERING_JOBS)
    lv = {j: min_level for j in jobs}
    lv[jobs[-1]] = min(200, min_level + spread)
    return lv


class BucketTest(unittest.TestCase):
    def test_min_level_bounds(self):
        for lvl, expected in [(1, "low"), (9, "low"), (10, "early"), (29, "early"),
                              (30, "mid"), (59, "mid"), (60, "high"), (99, "high"),
                              (100, "veryhigh"), (199, "veryhigh"), (200, "max")]:
            self.assertEqual(bucket_for(lvl, 0, FAKE)[0], expected, lvl)

    def test_spread_bounds(self):
        for spread, expected in [(0, "flat"), (4, "flat"), (5, "small"), (19, "small"),
                                 (20, "medium"), (49, "medium"), (50, "large"), (300, "large")]:
            self.assertEqual(bucket_for(45, spread, FAKE)[1], expected, spread)


class ResolveTest(unittest.TestCase):
    def test_deterministic(self):
        a = resolve_engine(_levels(5, 75), "levels", FAKE)
        b = resolve_engine(_levels(5, 75), "levels", FAKE)
        self.assertEqual(a, b)

    def test_known_bucket(self):
        r = resolve_engine(_levels(5, 75), "levels", FAKE)   # low + large
        self.assertEqual(r["engine"], "mcts")
        self.assertEqual(r["source"], "policy")
        self.assertEqual(r["bucket"], "levels|low|large")

    def test_lambda_from_table(self):
        r = resolve_engine(_levels(45, 0), "xp", FAKE)       # mid + flat
        self.assertEqual(r["engine"], "beam")
        self.assertEqual(r["lambda_travel"], 2.0)

    def test_missing_key_falls_back_to_default(self):
        r = resolve_engine(_levels(80, 0), "levels", FAKE)   # high|flat absent
        self.assertEqual(r["source"], "default")
        self.assertEqual(r["engine"], "beam")
        self.assertEqual(r["lambda_travel"], 1.0)

    def test_all_200_short_circuit(self):
        r = resolve_engine({j: 200 for j in GATHERING_JOBS}, "levels", FAKE)
        self.assertEqual(r["source"], "default")
        self.assertIn("max", r["bucket"])

    def test_metric_normalised(self):
        # An unknown metric is treated as "levels".
        self.assertEqual(resolve_engine(_levels(5, 75), "garbage", FAKE)["bucket"],
                         "levels|low|large")

    def test_contract(self):
        r = resolve_engine(_levels(20, 12), "xp", FAKE)
        self.assertEqual(set(r), {"engine", "lambda_travel", "source", "bucket"})
        self.assertIn(r["engine"], ("beam", "mcts"))
        self.assertIsInstance(r["lambda_travel"], float)
        self.assertGreaterEqual(r["lambda_travel"], 0.0)


class FallbackTest(unittest.TestCase):
    def test_absent_file_uses_builtin(self):
        policy = load_policy("/nonexistent/engine_policy.json")
        self.assertEqual(policy["default"]["engine"], "beam")
        # Resolving against it never raises and yields the default.
        r = resolve_engine(_levels(5, 75), "levels", policy)
        self.assertEqual(r["source"], "default")


class CommittedPolicyTest(unittest.TestCase):
    """Structural guard on the generated table — catches a malformed/stale file
    without asserting optimality (which legitimately shifts with the dataset)."""

    def test_policy_json_valid(self):
        self.assertTrue(os.path.exists(_POLICY_PATH),
                        "data/engine_policy.json missing — run "
                        "`python3 scripts/bench_routes.py --emit-policy > data/engine_policy.json`")
        with open(_POLICY_PATH, encoding="utf-8") as fh:
            policy = json.load(fh)
        self.assertEqual(policy["schema"], 1)
        min_labels = {row[2] for row in policy["min_level_buckets"]}
        spread_labels = {row[2] for row in policy["spread_buckets"]}
        self.assertGreater(len(policy["table"]), 0)
        for key, entry in policy["table"].items():
            metric, mlabel, slabel = key.split("|")
            self.assertIn(metric, ("levels", "xp"), key)
            self.assertIn(mlabel, min_labels, key)
            self.assertIn(slabel, spread_labels, key)
            self.assertIn(entry["engine"], ("beam", "mcts"), key)
            self.assertIsInstance(entry["lambda_travel"], (int, float), key)


class WebAutoTest(unittest.TestCase):
    def test_plan_auto(self):
        from webapp import app
        out = app.plan({"engine": "auto", "metric": "levels", "horizon": 3,
                        "job_levels": {"lumberjack": 9, "miner": 10, "herbalist": 62,
                                       "farmer": 88, "fisherman": 25}})
        self.assertIsNotNone(out["auto"])
        self.assertIn(out["engine"], ("beam", "mcts"))
        self.assertEqual(out["engine"], out["auto"]["engine"])
        # The window contract is unchanged regardless of the chosen engine.
        for stop in out["window"]:
            self.assertLessEqual(
                {"world_coords", "directions", "harvests", "value", "travel"},
                set(stop))


if __name__ == "__main__":
    unittest.main()
