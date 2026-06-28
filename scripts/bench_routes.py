#!/usr/bin/env python3
"""Route-quality benchmark for the leveling planners (greedy / beam / MCTS).

There is no single "right answer" for a leveling route (the problem is NP-hard),
so we measure *leveling speed* — ``rate = total_value / screens`` (percent-of-a-
level, or raw xp, gained per map-screen walked) — by simulating the **real rolling
loop** the web UI drives: plan a window, commit its first stop (harvest + move on),
re-plan, repeat. The engine that reaches more value per screen is the better one.

Run:  python3 scripts/bench_routes.py
It only reads the dataset and prints a table; it writes nothing.
"""

from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dofusjobs import (  # noqa: E402
    GATHERING_JOBS,
    FarmLoopFinder,
    JobXpTable,
    load_dataset,
)

# Fixed scenarios: a few starting-level mixes (the unbalanced ones are where
# lookahead matters most), each crossed with metric and travel weight.
LEVEL_SETS = {
    "all=1": {j: 1 for j in GATHERING_JOBS},
    "all=50": {j: 50 for j in GATHERING_JOBS},
    "mix": {"lumberjack": 9, "miner": 10, "herbalist": 62, "farmer": 88, "fisherman": 25},
}
METRICS = ("levels", "xp")
LAMBDAS = (0.5, 1.0, 2.0)

K_STEPS = 30          # committed stops per rolling simulation
HORIZON = 20          # lookahead window for beam / MCTS


def _roll(plan, finder, job_xp, k):
    """Drive a planner through ``k`` committed stops of the interactive rolling
    loop, accumulating realised value and screens. ``plan(pos, job_xp, visited)``
    returns a window; we commit window[0] each step (harvest + advance), exactly
    like the web UI's ``Suivant`` button."""
    job_xp = dict(job_xp)
    visited: list = []
    pos = None
    value = 0.0
    screens = 0
    elapsed = 0.0
    calls = 0
    for _ in range(k):
        t0 = time.perf_counter()
        window = plan(pos, job_xp, visited)
        elapsed += time.perf_counter() - t0
        calls += 1
        if not window:
            break
        first = window[0]
        value += first["value"]
        screens += first.get("travel", 0)
        job_xp, visited = finder.advance(job_xp, visited, first["world_coords"])
        pos = first["world_coords"]
    rate = value / screens if screens else value
    return {"value": value, "screens": screens, "rate": rate,
            "ms": 1000.0 * elapsed / max(1, calls)}


def _engines(finder, metric, lam):
    """The three planners as uniform ``(pos, job_xp, visited) -> window`` callables.
    Greedy is the beam with a one-step, width-one lookahead (i.e. myopic)."""
    def greedy(pos, jx, vis):
        return finder.plan_window(pos, jx, vis, horizon=1, metric=metric,
                                  lambda_travel=lam, beam_width=1, branch=1)

    def beam(pos, jx, vis):
        return finder.plan_window(pos, jx, vis, horizon=HORIZON, metric=metric,
                                  lambda_travel=lam)

    def mcts(pos, jx, vis):
        return finder.plan_window_mcts(pos, jx, vis, horizon=HORIZON, metric=metric,
                                       lambda_travel=lam)

    return {"greedy": greedy, "beam": beam, "mcts": mcts}


def main() -> None:
    xp_table = JobXpTable.load()
    resources, cells, maps = load_dataset()
    finder = FarmLoopFinder(resources, cells, maps=maps, xp_table=xp_table)

    hdr = (f"{'scenario':<22}{'engine':<8}{'rate':>10}{'value':>11}"
           f"{'screens':>9}{'ms/call':>9}")
    print(hdr, flush=True)
    print("-" * len(hdr), flush=True)
    wins = {"greedy": 0, "beam": 0, "mcts": 0}
    for set_name, levels in LEVEL_SETS.items():
        job_xp = {j: xp_table.xp_for_level(levels[j]) for j in GATHERING_JOBS}
        for metric in METRICS:
            for lam in LAMBDAS:
                scen = f"{set_name}/{metric}/λ{lam}"
                rates = {}
                for name, plan in _engines(finder, metric, lam).items():
                    r = _roll(plan, finder, job_xp, K_STEPS)
                    rates[name] = r["rate"]
                    print(f"{scen:<22}{name:<8}{r['rate']:>10.4f}{r['value']:>11.2f}"
                          f"{r['screens']:>9d}{r['ms']:>9.1f}", flush=True)
                best = max(rates, key=lambda n: rates[n])
                wins[best] += 1
                print(f"{'':<22}{'-> best: ' + best:<8}", flush=True)
    print("-" * len(hdr), flush=True)
    print(f"best-engine tally over {sum(wins.values())} scenarios: {wins}", flush=True)


if __name__ == "__main__":
    main()
