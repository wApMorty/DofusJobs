"""CLI: best gathering leveling route (v4 — score maps by total %XP, no pods).

Each map is scored by the total %-of-a-level it yields at your current levels;
the route greedily walks the best score-per-screen path, harvesting whole maps
and simulating level-ups, to level all jobs as fast as possible.

Examples:
  python -m dofusjobs --lumberjack 9 --miner 10 --herbalist 62 --farmer 88 --fisherman 25
  python -m dofusjobs --all 1 --lambda 0.5          # denser, longer route
  python -m dofusjobs --all 50 --metric xp --json
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter

from . import GATHERING_JOBS, JOB_LABELS_FR, plan_farm_route


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="dofusjobs", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--lambda", dest="lam", type=float, default=1.0,
                   help="travel penalty: %%XP traded per screen (higher = shorter route); default 1.0")
    p.add_argument("--metric", choices=("levels", "xp"), default="levels",
                   help="map score: 'levels' (%% of a level, balances jobs) or 'xp' (raw XP)")
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

    r = plan_farm_route(job_levels=levels, metric=args.metric, lambda_travel=args.lam)

    if args.json:
        print(json.dumps(r.to_dict(), indent=2, ensure_ascii=False))
        return 0

    unit = "%-lvl" if r.metric == "levels" else "xp"
    print(f"\nLeveling route [{r.metric}] — {len(r.stops)} stops, {r.screens} screens, "
          f"+{r.total_value:.1f} {unit} total, rate {r.rate:.2f} {unit}/screen "
          f"(lambda={args.lam})")
    print(f"Terminated: {r.terminated}")
    print("Per job: " + ", ".join(
        f"{JOB_LABELS_FR[j]} {r.start_levels[j]}->{r.end_levels[j]} "
        f"(+{v / 100:.2f} lvl)" if r.metric == "levels"
        else f"{JOB_LABELS_FR[j]} +{v:.0f} xp"
        for j, v in sorted(r.per_job.items(), key=lambda kv: -kv[1])) or "  (none)")

    print("\nRoute:")
    for i, stop in enumerate(r.stops):
        c = Counter()
        for h in stop.harvests:
            c[h["resource_name"]] += h["quantity"]
        acts = ", ".join(f"{n}x{cnt}" for n, cnt in c.items()) or "-"
        move = "depart" if i == 0 else (" ".join(stop.directions) or "sur place")
        print(f"  {i:>3}. {str(list(stop.world_coords)):<11} {stop.value:6.2f}  [{acts}]")
        print(f"       ↳ {move}")
        for t in stop.transit:                       # free pick-ups on the way
            tc = Counter()
            for h in t["harvests"]:
                tc[h["resource_name"]] += h["quantity"]
            acts_t = ", ".join(f"{n}x{cnt}" for n, cnt in tc.items())
            print(f"         · au passage {t['world_coords']}: {acts_t}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
