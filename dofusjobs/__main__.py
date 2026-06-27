"""CLI: compute an optimized gathering route (v2: free start + level-up sim).

Examples:
  python -m dofusjobs --pods 1000 --lambda 2 --lumberjack 60 --miner 40
  python -m dofusjobs --pods 400 --all 1 --json         # low level: watch unlocks
  python -m dofusjobs --pods 500 --all 50 --start 10,-20 --online
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter

from . import GATHERING_JOBS, JOB_LABELS_FR, plan_route


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="dofusjobs", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--pods", type=int, required=True, help="pods limit for the pass")
    p.add_argument("--lambda", dest="lam", type=float, default=1.0,
                   help="travel penalty weight (XP per map-screen); default 1.0")
    p.add_argument("--metric", choices=("levels", "xp"), default="levels",
                   help="objective: 'levels' (%% of a level, balances jobs) or 'xp' (raw)")
    p.add_argument("--start", type=str, default=None,
                   help="start coords 'x,y' (default: optimizer chooses)")
    p.add_argument("--json", action="store_true", help="emit JSON")
    p.add_argument("--all", type=int, metavar="LVL", help="set every job to this level")
    for job in GATHERING_JOBS:
        p.add_argument(f"--{job}", type=int, default=None,
                       help=f"{JOB_LABELS_FR[job]} level")
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    levels = {j: (getattr(args, j) if getattr(args, j) is not None
                  else (args.all if args.all is not None else 1))
              for j in GATHERING_JOBS}
    start = None
    if args.start:
        x, y = args.start.split(",")
        start = (int(x), int(y))

    result = plan_route(job_levels=levels, pods_limit=args.pods,
                        lambda_travel=args.lam, metric=args.metric,
                        start_coords=start)

    if args.json:
        print(json.dumps(result.to_dict(), indent=2, ensure_ascii=False))
        return 0

    print(f"\nOptimized pass [{result.metric}] — pods {result.pods_used}/{result.pods_limit}, "
          f"XP {result.total_xp}, +{result.total_levels_gained:.2f} levels, "
          f"travel {result.total_travel_cost}, score {result.score:.1f} (lambda={result.lambda_travel})")
    print(f"Terminated: {result.terminated_reason}")
    print("Per job: " + ", ".join(
        f"{JOB_LABELS_FR[j]} {result.start_levels[j]}->{result.end_levels[j]} "
        f"(+{result.levels_gained.get(j, 0.0):.2f} lvl, {result.xp_by_job.get(j, 0)} xp)"
        for j in GATHERING_JOBS if result.xp_by_job.get(j)) or "  (none)")

    print("\nRoute:")
    for i, stop in enumerate(result.route):
        c = Counter(h.resource_name for h in stop.harvests)
        acts = ", ".join(f"{n}x{cnt}" for n, cnt in c.items()) or "-"
        move = "depart" if i == 0 else (" ".join(stop.directions) or "sur place")
        print(f"  {i:>2}. {str(list(stop.world_coords)):<11} "
              f"+{stop.travel_cost_from_prev:<3}travel  {stop.stop_xp:>5}xp  [{acts}]")
        print(f"      ↳ {move}")

    if result.level_ups:
        print("\nLevel-ups during the pass (path adapts):")
        for lu in result.level_ups:
            unl = f"  unlocks: {', '.join(lu.unlocked)}" if lu.unlocked else ""
            print(f"  @stop {lu.at_stop_index}: {JOB_LABELS_FR[lu.job_id]} "
                  f"{lu.from_level}->{lu.to_level}{unl}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
