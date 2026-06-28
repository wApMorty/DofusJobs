"""Depletion simulation: does the feedback-adaptive availability model beat an
availability-blind baseline when bots empty the dense maps?

We build a tiny world where one very dense "hub" map (high catalog value) sits
behind a short detour and is emptied by bots almost every lap, while a line of
modest but reliable field maps is always full. A player runs several laps with
the rolling planner:

  * ADAPTIVE  -> when a map is found empty the player reports it (EWMA obs=0), so
                 the learned availability feeds the next planning step and the
                 route drifts off the chronically-empty hub onto the fields.
  * BASELINE  -> identical engine but availability is never learned, so the raw
                 catalog value keeps sending the player to the empty hub each lap.

The hidden per-map empty schedule is drawn once from ``random.Random(seed)`` and
shared by both runs, so the comparison is fully deterministic. We report the
realized leveling speed (%XP actually banked per screen actually walked); the
adaptive run should be the faster one.

Run:  python3 scripts/sim_depletion.py
"""

from __future__ import annotations

import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dofusjobs.farmloop import FarmLoopFinder, ewma_update  # noqa: E402
from dofusjobs.leveling import JobXpTable  # noqa: E402
from dofusjobs.models import GATHERING_JOBS, Cell, CellResource, Resource  # noqa: E402


_HUB = (2, 4)
_START = (0, 0)


def _fixture():
    """A field line x=0..4 at y=0 (reliable, modest) plus a dense hub at (2,4)
    behind a four-map dead-end branch up x=2 (a costly detour). The hub's huge
    catalog value lures the route up the branch every lap for almost no real
    yield. Returns (finder, hub_coord, empty_prob_by_coord)."""
    res = {
        "field": Resource("field", "Frene", "lumberjack", 18, 1, 1, 5),
        "rich": Resource("rich", "Chene", "lumberjack", 18, 1, 1, 5),
    }
    cells = [Cell(f"f{x}", (x, 0), (CellResource("field", 4),)) for x in range(5)]
    cells.append(Cell("hub", _HUB, (CellResource("rich", 120),)))   # dense bot magnet
    maps = [(x, 0) for x in range(5)] + [(2, 1), (2, 2), (2, 3), (2, 4)]
    finder = FarmLoopFinder(res, cells, maps=maps, xp_table=JobXpTable.load())
    empty_prob = {(x, 0): 0.0 for x in range(5)}
    empty_prob[_HUB] = 0.85                         # hub emptied by bots most laps
    return finder, _HUB, empty_prob


def _empty_schedule(empty_prob, laps, seed):
    """Deterministic per-(coord, lap) emptiness, drawn once and shared by both
    runs so the only difference between them is whether availability is learned."""
    rng = random.Random(seed)
    sched = {}
    for coord in sorted(empty_prob):               # sorted => stable draw order
        p = empty_prob[coord]
        sched[coord] = [rng.random() < p for _ in range(laps)]
    return sched


def _run(finder, sched, laps, adaptive, horizon=4, steps_per_lap=8):
    xp_table = finder.xp_table
    avail = {}
    realized = 0.0          # %XP actually banked
    screens = 0             # screens actually walked
    for lap in range(laps):
        pos, visited = _START, []        # start each lap on the field line, not free
        job_xp = {j: xp_table.xp_for_level(1) for j in GATHERING_JOBS}
        for _ in range(steps_per_lap):
            window = finder.plan_window(pos, job_xp, visited, horizon=horizon,
                                        lambda_travel=0.5,
                                        availability=(avail if adaptive else None))
            if not window:
                break
            stop = window[0]
            coord = (int(stop["world_coords"][0]), int(stop["world_coords"][1]))
            screens += int(stop.get("travel", 0))   # you walked there either way
            if sched.get(coord, [False] * laps)[lap]:      # arrived to an empty map
                if adaptive:
                    avail[coord] = ewma_update(avail.get(coord, 1.0), 0.0)
            else:                                          # full: harvest it for real
                before = sum(xp_table.level_progress(job_xp[j]) for j in GATHERING_JOBS)
                job_xp = finder.harvest_coord(job_xp, coord)
                after = sum(xp_table.level_progress(job_xp[j]) for j in GATHERING_JOBS)
                realized += 100.0 * (after - before)
                if adaptive:
                    avail[coord] = ewma_update(avail.get(coord, 1.0), 1.0)
            visited.append(list(coord))
            pos = coord
    return realized / screens if screens else 0.0, realized, screens


def run(seed: int = 0, laps: int = 10):
    """Run both engines on the same seeded depletion schedule. Returns
    (adaptive_rate, baseline_rate, detail dict)."""
    finder, hub, empty_prob = _fixture()
    sched = _empty_schedule(empty_prob, laps, seed)
    a_rate, a_val, a_scr = _run(finder, sched, laps, adaptive=True)
    b_rate, b_val, b_scr = _run(finder, sched, laps, adaptive=False)
    return a_rate, b_rate, {
        "hub": hub, "laps": laps, "seed": seed,
        "adaptive": {"rate": a_rate, "value": a_val, "screens": a_scr},
        "baseline": {"rate": b_rate, "value": b_val, "screens": b_scr},
    }


def main():
    a_rate, b_rate, d = run()
    print(f"Depletion simulation (seed={d['seed']}, laps={d['laps']}, "
          f"hub={d['hub']} emptied ~85% of laps)\n")
    print(f"  baseline (availability-blind) : {b_rate:.4f} %XP/screen  "
          f"({d['baseline']['value']:.1f} %XP over {d['baseline']['screens']} screens)")
    print(f"  adaptive (feedback EWMA)      : {a_rate:.4f} %XP/screen  "
          f"({d['adaptive']['value']:.1f} %XP over {d['adaptive']['screens']} screens)")
    ratio = (a_rate / b_rate) if b_rate else float("inf")
    print(f"\n  adaptive is {ratio:.2f}x the baseline realized leveling speed.")
    print("  => " + ("adaptive WINS" if a_rate >= b_rate * 1.05 else "no clear win"))


if __name__ == "__main__":
    main()
