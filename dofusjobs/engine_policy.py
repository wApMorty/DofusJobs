"""Auto engine/lambda selection from job levels (the web UI's ``engine=auto``).

The route-quality A/B benchmark (``scripts/bench_routes.py``) shows the best
planner (beam vs mcts) and the best travel weight (lambda) depend on the *level
regime*: mcts tends to win at low, unbalanced levels while beam wins on the ``xp``
metric and on uniform mid-levels. ``scripts/bench_routes.py --emit-policy`` bakes
that verdict into ``data/engine_policy.json`` -- a small lookup table keyed by a
coarse (metric, min-level bucket, spread bucket) feature. This module reads that
table and resolves a config in O(1); it never blocks the request loop.

Robustness: a missing/corrupt file, an unknown key, or an all-200 input never
raise -- they fall back to the built-in default (beam, lambda=1), i.e. the
historical behaviour. So a stale table can only pick a slightly sub-optimal
engine, never break the app.
"""

from __future__ import annotations

import json
import os
from typing import Dict, List, Optional, Tuple

_DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
_POLICY_PATH = os.path.join(_DATA_DIR, "engine_policy.json")

# Built-in fallbacks (also the canonical bucket bounds the generator must use).
# Each row is [lo, hi, label] with both bounds inclusive; the top row's hi is a
# catch-all so any level 1..200 / any spread maps to exactly one bucket.
_BUILTIN_MIN_BUCKETS: List[list] = [
    [1, 9, "low"], [10, 29, "early"], [30, 59, "mid"],
    [60, 99, "high"], [100, 199, "veryhigh"], [200, 200, "max"],
]
_BUILTIN_SPREAD_BUCKETS: List[list] = [
    [0, 4, "flat"], [5, 19, "small"], [20, 49, "medium"], [50, 9999, "large"],
]
_BUILTIN_DEFAULT: Dict = {"engine": "beam", "lambda_travel": 1.0}

# Module-level cache (like _FINDER/_XP_TABLE in app.py): the table is immutable.
_POLICY_CACHE: Dict[str, Dict] = {}


def _builtin_policy() -> Dict:
    return {
        "schema": 1,
        "min_level_buckets": _BUILTIN_MIN_BUCKETS,
        "spread_buckets": _BUILTIN_SPREAD_BUCKETS,
        "default": dict(_BUILTIN_DEFAULT),
        "table": {},
    }


def load_policy(path: Optional[str] = None) -> Dict:
    """Load (and cache) the policy table. Any read/parse error -> built-in
    fallback, so callers never have to guard against a missing file."""
    key = path or _POLICY_PATH
    if key in _POLICY_CACHE:
        return _POLICY_CACHE[key]
    try:
        with open(key, "r", encoding="utf-8") as fh:
            policy = json.load(fh)
        if not isinstance(policy, dict) or "table" not in policy:
            raise ValueError("malformed policy")
        policy.setdefault("min_level_buckets", _BUILTIN_MIN_BUCKETS)
        policy.setdefault("spread_buckets", _BUILTIN_SPREAD_BUCKETS)
        policy.setdefault("default", dict(_BUILTIN_DEFAULT))
    except Exception:       # noqa: BLE001  (any failure -> safe fallback)
        policy = _builtin_policy()
    _POLICY_CACHE[key] = policy
    return policy


def _label_for(value: int, buckets: List[list]) -> str:
    """First bucket [lo, hi, label] whose range contains ``value`` (bounds
    inclusive). The generator guarantees the top bucket is a catch-all; if a
    custom table somehow misses, fall back to the last bucket's label."""
    for lo, hi, label in buckets:
        if lo <= value <= hi:
            return label
    return buckets[-1][2]


def bucket_for(min_level: int, spread: int, policy: Optional[Dict] = None) -> Tuple[str, str]:
    """(min-level label, spread label) for the given features."""
    policy = policy or load_policy()
    return (
        _label_for(int(min_level), policy.get("min_level_buckets", _BUILTIN_MIN_BUCKETS)),
        _label_for(int(spread), policy.get("spread_buckets", _BUILTIN_SPREAD_BUCKETS)),
    )


def resolve_engine(job_levels: Dict[str, int], metric: str,
                   policy: Optional[Dict] = None) -> Dict:
    """Pick (engine, lambda_travel) for the given job levels and metric.

    Returns ``{engine, lambda_travel, source, bucket}`` where ``source`` is
    "policy" (matched a table entry) or "default" (fell back). Deterministic:
    pure lookup, no RNG and no timing in the decision path.
    """
    policy = policy or load_policy()
    default = policy.get("default", _BUILTIN_DEFAULT)
    metric = "xp" if metric == "xp" else "levels"

    levels = [int(v) for v in job_levels.values()] or [1]
    min_level, max_level = min(levels), max(levels)
    spread = max_level - min_level
    min_label, spread_label = bucket_for(min_level, spread, policy)
    key = f"{metric}|{min_label}|{spread_label}"

    # All jobs maxed: nothing left to farm; don't bother with the table.
    if min_label == "max":
        return {"engine": default["engine"], "lambda_travel": float(default["lambda_travel"]),
                "source": "default", "bucket": key}

    entry = policy.get("table", {}).get(key)
    if entry is None:
        return {"engine": default["engine"], "lambda_travel": float(default["lambda_travel"]),
                "source": "default", "bucket": key}
    engine = "mcts" if entry.get("engine") == "mcts" else "beam"
    return {"engine": engine, "lambda_travel": float(entry.get("lambda_travel", 1.0)),
            "source": "policy", "bucket": key}
