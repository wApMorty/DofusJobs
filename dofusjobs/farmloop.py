"""Greedy leveling-route finder (v4): maximise leveling SPEED, not inventory.

Rationale (replaces the pods-capped orienteering of v2/v3): for *leveling* a
gathering job the real constraint isn't inventory weight — you bank as often as
needed — it's TIME, i.e. travel. So a map is scored by the total %-of-a-level it
yields (every eligible resource on it, all jobs summed, at the current levels),
NOT divided by pods (which was what made heavy wood/ore always lose). Low jobs
weigh most (a level is cheap), so they're prioritised; as a job climbs, its score
shrinks, steering the route to bring every job toward 200.

Algorithm:
  1. value(map) = Σ %-of-a-level of every eligible resource on it (current levels);
  2. restrict to the richest connected component (a walking route can't leave it);
  3. greedily walk to the best (value − λ·travel) un-visited map, harvest it whole,
     simulate the level-ups, repeat until no nearby map is worth the walk;
  4. emit the ordered route with per-stop harvests and arrow directions.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .leveling import JobXpTable
from .mapgraph import MapGraph
from .models import GATHERING_JOBS, Cell, Coord, Resource

_REACH = 45          # per-step search radius in screens (keeps each step cheap)
_MAX_STOPS = 600     # safety cap on total route length


@dataclass
class LoopStop:
    world_coords: Coord
    directions: List[str]
    harvests: List[dict]            # {resource_id, resource_name, job_id, quantity, xp}
    value: float                    # %-levels (or xp) gained here per lap


@dataclass
class FarmLoopResult:
    stops: List[LoopStop] = field(default_factory=list)
    screens: int = 0                # total screens walked
    total_value: float = 0.0        # total %-levels (or raw xp) gained
    rate: float = 0.0               # total_value / screens (leveling speed)
    per_job: Dict[str, float] = field(default_factory=dict)
    metric: str = "levels"
    start_levels: Dict[str, int] = field(default_factory=dict)
    end_levels: Dict[str, int] = field(default_factory=dict)
    terminated: str = ""

    def to_dict(self) -> dict:
        return {
            "metric": self.metric,
            "screens": self.screens,
            "total_value": round(self.total_value, 4),
            "rate": round(self.rate, 5),
            "per_job": {k: round(v, 4) for k, v in self.per_job.items()},
            "start_levels": self.start_levels,
            "end_levels": self.end_levels,
            "terminated": self.terminated,
            "stops": [
                {"world_coords": list(s.world_coords), "directions": s.directions,
                 "value": round(s.value, 4), "harvests": s.harvests}
                for s in self.stops
            ],
        }


class FarmLoopFinder:
    def __init__(self, resources: Dict[str, Resource], cells: List[Cell],
                 maps=None, graph: Optional[MapGraph] = None,
                 xp_table: Optional[JobXpTable] = None) -> None:
        self.resources = resources
        self.cells = [c for c in cells if c.resources]
        self.cells_by_coord = {c.world_coords: c for c in self.cells}
        self.xp_table = xp_table or JobXpTable.load()
        if graph is not None:
            self.graph = graph
        else:
            nodes = list(maps) if maps else [c.world_coords for c in self.cells]
            nodes += [c.world_coords for c in self.cells]
            self.graph = MapGraph(nodes)

    # ---- per-map value (job-weighted %-of-a-level; no pods) -------------------
    def _cell_value(self, cell: Cell, job_levels, job_xp, metric, weights, only_job=None):
        total = 0.0
        per = {}
        items = []
        for cr in cell.resources:
            r = self.resources[cr.resource_id]
            if only_job and r.job_id != only_job:
                continue
            if job_levels.get(r.job_id, 0) < r.required_level:
                continue
            gain_xp = r.base_xp * cr.quantity
            if metric == "xp":
                g = float(gain_xp)
            else:
                b = self.xp_table.level_progress(job_xp[r.job_id])
                a = self.xp_table.level_progress(job_xp[r.job_id] + gain_xp)
                g = 100.0 * (a - b)
            g *= weights.get(r.job_id, 1.0)
            total += g
            per[r.job_id] = per.get(r.job_id, 0.0) + g
            items.append({"resource_id": r.resource_id, "resource_name": r.name,
                          "job_id": r.job_id, "quantity": cr.quantity, "xp": r.base_xp})
        return total, per, items

    def _richest_component(self, levels, metric):
        """Cells of the connected component with the most total %XP value at
        these levels — a walking route can't leave its component (boat/zaap-only
        islands), so anchor in the richest one."""
        comps = self.graph.components()
        if len(comps) <= 1:
            return self.cells
        cc = {co: i for i, comp in enumerate(comps) for co in comp}
        job_xp = {j: self.xp_table.xp_for_level(levels.get(j, 1)) for j in GATHERING_JOBS}
        val = [0.0] * len(comps)
        for c in self.cells:
            i = cc.get(c.world_coords)
            if i is None:
                continue
            v, _, _ = self._cell_value(c, levels, job_xp, metric, {})
            val[i] += v
        keep = comps[max(range(len(comps)), key=lambda i: val[i])]
        return [c for c in self.cells if c.world_coords in keep]

    def find(self, job_levels: Dict[str, int], metric: str = "levels",
             lambda_travel: float = 1.0, max_stops: int = _MAX_STOPS) -> FarmLoopResult:
        """Greedy leveling route (no pods). Each map is scored by the TOTAL
        %-of-a-level it yields — every eligible resource on it, all jobs summed,
        at the current levels. We repeatedly walk to the best score-minus-travel
        map, harvest it whole, and simulate the level-ups; a job's score shrinks
        as it climbs, so the route balances all jobs toward 200. Low jobs weigh
        most (a level is cheap), so they're prioritised. Stops when no nearby map
        is worth the walk (``lambda_travel`` = %XP traded per screen)."""
        metric = "xp" if metric == "xp" else "levels"
        lam = max(0.0, float(lambda_travel))
        job_xp = {j: self.xp_table.xp_for_level(job_levels.get(j, 1)) for j in GATHERING_JOBS}
        start_xp = dict(job_xp)
        cur_levels = {j: self.xp_table.level_for_xp(job_xp[j]) for j in GATHERING_JOBS}

        comp_cells = self._richest_component(cur_levels, metric)
        visited: set = set()
        pos: Optional[Coord] = None
        stops: List[LoopStop] = []
        screens = 0
        terminated = "no_eligible_spot"

        while len(stops) < max_stops:
            if pos is None:
                reachable = ((c, 0) for c in comp_cells)
            else:
                d = self.graph.distances_from(pos, max_dist=_REACH)
                reachable = ((c, d[c.world_coords]) for c in comp_cells
                             if c.world_coords in d)
            best = None                       # (key, cell, travel, items)
            for c, travel in reachable:
                if c.world_coords in visited:
                    continue
                v, _per, items = self._cell_value(c, cur_levels, job_xp, metric, {})
                if v <= 0:
                    continue
                score = v - lam * travel
                if score <= 0:
                    continue
                key = (score, -travel, c.world_coords)
                if best is None or key > best[0]:
                    best = (key, c, travel, items, v)
            if best is None:
                terminated = "no_positive_gain" if pos is not None else "no_eligible_spot"
                break
            _, c, travel, items, v = best
            dirs = self.graph.directions(pos, c.world_coords) if pos is not None else []
            screens += travel
            visited.add(c.world_coords)
            pos = c.world_coords
            for it in items:                  # harvest the whole map; simulate level-ups
                job = it["job_id"]
                job_xp[job] += it["xp"] * it["quantity"]
                lvl = self.xp_table.level_for_xp(job_xp[job])
                if lvl > cur_levels[job]:
                    cur_levels[job] = lvl
            stops.append(LoopStop(world_coords=pos, directions=dirs,
                                  harvests=items, value=v))
        else:
            terminated = "max_stops"

        per_job: Dict[str, float] = {}
        for j in GATHERING_JOBS:
            g = (job_xp[j] - start_xp[j] if metric == "xp"
                 else 100.0 * (self.xp_table.level_progress(job_xp[j])
                               - self.xp_table.level_progress(start_xp[j])))
            if g:
                per_job[j] = g
        total = sum(per_job.values())
        return FarmLoopResult(
            stops=stops, screens=screens, total_value=total,
            rate=(total / screens) if screens else total, per_job=per_job,
            metric=metric, terminated=terminated,
            start_levels={j: self.xp_table.level_for_xp(start_xp[j]) for j in GATHERING_JOBS},
            end_levels=dict(cur_levels))
