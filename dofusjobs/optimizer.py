"""Sequential single-pass optimizer with in-plan level-up simulation (v2).

Objective (weighted):  score = total_xp - lambda * total_travel_cost
subject to a hard pods cap and live job-level gating.

Why sequential (not the v1 select-then-order split): because levels rise as you
harvest, *which* resources are eligible depends on the order and timing of
harvests. So the route is built step by step, simulating XP accumulation:

  * free start: the first harvest pays no travel (you begin there);
  * each step picks the most pods-efficient eligible harvest available *now*
    (travel folded into the value), harvests a chunk, then re-evaluates levels;
  * crossing a level threshold can unlock higher-tier resources, which then
    become candidates for the remaining pods -> the path adapts mid-pass.

Walking cost between cells is A* over the world-map grid. Deterministic
tie-breaking: higher efficiency, then higher value, then lower travel, then
cell_id, then resource_id.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from .leveling import JobXpTable
from .mapgraph import MapGraph
from .models import (
    GATHERING_JOBS,
    Cell,
    Coord,
    Harvest,
    LevelUp,
    PlayerInput,
    Resource,
    RouteResult,
    RouteStop,
)


class Optimizer:
    def __init__(self, resources: Dict[str, Resource], cells: List[Cell],
                 maps: Optional[List] = None, graph: Optional[MapGraph] = None,
                 xp_table: Optional[JobXpTable] = None) -> None:
        self.resources = resources
        self.cells = [self._clean_cell(c) for c in cells]
        self.cells = [c for c in self.cells if c.resources]
        self.cells_by_coord = {c.world_coords: c for c in self.cells}
        # A farming pass stays local; only consider cells within this many
        # map-screens of the current position (keeps the per-step scan cheap and
        # the route realistic). The free-start step still scans everything once.
        self.reach = 45
        self.xp_table = xp_table or JobXpTable.load()
        if graph is not None:
            self.graph = graph
        else:
            # Graph nodes = the real maps (so A*/BFS follows the continent layout);
            # fall back to just the cell coords if no map set is supplied.
            node_coords = list(maps) if maps else [c.world_coords for c in self.cells]
            node_coords += [c.world_coords for c in self.cells]
            self.graph = MapGraph(node_coords)

    def _clean_cell(self, cell: Cell) -> Cell:
        kept = tuple(cr for cr in cell.resources
                     if cr.resource_id in self.resources and cr.quantity > 0)
        return Cell(cell.cell_id, cell.world_coords, kept)

    # ----------------------------------------------------------- cell analysis
    def cell_analysis(self, job_levels: Dict[str, int]) -> List[dict]:
        """Per-cell available XP and pods-to-clear at the given levels (the
        'XP/pods per cell' view)."""
        out = []
        for c in self.cells:
            xp = pods = 0
            for cr in c.resources:
                res = self.resources[cr.resource_id]
                if job_levels.get(res.job_id, 0) >= res.required_level:
                    xp += res.base_xp * cr.quantity
                    pods += res.pods * cr.quantity
            out.append({"cell_id": c.cell_id, "world_coords": list(c.world_coords),
                        "available_xp": xp, "pods_to_clear": pods})
        return out

    # -------------------------------------------------------------------- solve
    def optimize(self, player: PlayerInput) -> RouteResult:
        lam = max(0.0, float(player.lambda_travel))
        pods_limit = int(player.pods_limit)
        tbl = self.xp_table
        metric = "xp" if player.metric == "xp" else "levels"

        job_xp = {j: int(player.job_xp.get(j, 0)) for j in GATHERING_JOBS}
        start_xp = dict(job_xp)
        start_levels = {j: tbl.level_for_xp(job_xp[j]) for j in GATHERING_JOBS}

        def harvest_value(job_id: str, units: int, base_xp: int) -> float:
            """Worth of harvesting ``units`` now, in the active metric's unit.
            'levels' = percent of a level gained (state-dependent: a level costs
            more XP the higher you are, so high-level jobs yield less)."""
            if metric == "xp":
                return float(units * base_xp)
            before = tbl.level_progress(job_xp[job_id])
            after = tbl.level_progress(job_xp[job_id] + units * base_xp)
            return 100.0 * (after - before)

        def travel_penalty(job_id: str, travel: int) -> float:
            """Cost of ``travel`` screens in the active metric's unit. ``lambda``
            is XP-per-screen (per the spec); for 'levels' that XP is converted to
            % of a level at the job's *current* XP, so the penalty shrinks with
            level exactly like ``harvest_value`` does. Without this the % gains
            (which scale ~1/level) would be crushed by a raw lambda at high level,
            stalling the route with pods to spare."""
            cost_xp = lam * travel
            if metric == "xp" or cost_xp == 0:
                return cost_xp
            before = tbl.level_progress(job_xp[job_id])
            after = tbl.level_progress(job_xp[job_id] + cost_xp)
            return 100.0 * (after - before)

        # Mutable remaining quantities per (cell_id, resource_id).
        remaining: Dict[Tuple[str, str], int] = {}
        cell_by_id: Dict[str, Cell] = {}
        for c in self.cells:
            cell_by_id[c.cell_id] = c
            for cr in c.resources:
                remaining[(c.cell_id, cr.resource_id)] = cr.quantity

        pos: Optional[Coord] = player.start_coords
        pods_left = pods_limit
        route: List[RouteStop] = []
        level_ups: List[LevelUp] = []
        xp_by_job = {j: 0 for j in GATHERING_JOBS}
        total_xp = 0
        terminated_reason = "no_eligible_spot"

        def eligible_resource_ids(levels: Dict[str, int]) -> set:
            ids = set()
            for r in self.resources.values():
                if levels.get(r.job_id, 0) >= r.required_level:
                    ids.add(r.resource_id)
            return ids

        cur_levels = dict(start_levels)
        prev_eligible = eligible_resource_ids(cur_levels)

        while True:
            candidates = []
            any_eligible = any_affordable = False
            # One bounded BFS from the current position gives travel to every
            # nearby cell; iterate only those (far cells are never worth it).
            if pos is None:
                cell_travel = [(c, 0) for c in self.cells]
            else:
                dist = self.graph.distances_from(pos, max_dist=self.reach)
                cell_travel = [(self.cells_by_coord[co], d)
                               for co, d in dist.items() if co in self.cells_by_coord]
            for c, travel in cell_travel:
                for cr in c.resources:
                    qty = remaining[(c.cell_id, cr.resource_id)]
                    if qty <= 0:
                        continue
                    res = self.resources[cr.resource_id]
                    if cur_levels.get(res.job_id, 0) < res.required_level:
                        continue
                    any_eligible = True
                    if res.pods > pods_left:
                        continue
                    any_affordable = True
                    units = min(qty, pods_left // res.pods)
                    value = (harvest_value(res.job_id, units, res.base_xp)
                             - travel_penalty(res.job_id, travel))
                    if value <= 0:
                        continue
                    eff = value / (units * res.pods)
                    # Sort key: efficiency desc, value desc, travel asc, ids asc.
                    candidates.append(((-eff, -value, travel, c.cell_id, cr.resource_id),
                                       c.cell_id, cr.resource_id, units, travel, c.world_coords))

            if not candidates:
                if not any_eligible:
                    terminated_reason = "no_eligible_spot"
                elif not any_affordable:
                    terminated_reason = "pods_full"
                else:
                    terminated_reason = "no_positive_gain"
                break

            candidates.sort(key=lambda x: x[0])
            _, cell_id, res_id, units, travel, coords = candidates[0]
            res = self.resources[res_id]

            # Arrive at the cell (new stop if we moved / first stop).
            if not route or route[-1].cell_id != cell_id or route[-1].world_coords != coords:
                dirs = self.graph.directions(pos, coords) if pos is not None else []
                route.append(RouteStop(cell_id=cell_id, world_coords=coords,
                                       travel_cost_from_prev=travel, directions=dirs))
                pos = coords
            stop_index = len(route) - 1

            for _ in range(units):
                route[-1].harvests.append(Harvest(
                    resource_id=res.resource_id, resource_name=res.name,
                    job_id=res.job_id, xp=res.base_xp, pods=res.pods))
            gained = units * res.base_xp
            total_xp += gained
            xp_by_job[res.job_id] += gained
            pods_left -= units * res.pods
            remaining[(cell_id, res_id)] -= units

            # Level-up simulation: did this job cross a threshold?
            old_level = cur_levels[res.job_id]
            job_xp[res.job_id] += gained
            new_level = tbl.level_for_xp(job_xp[res.job_id])
            if new_level > old_level:
                cur_levels[res.job_id] = new_level
                now_eligible = eligible_resource_ids(cur_levels)
                unlocked = sorted(now_eligible - prev_eligible)
                prev_eligible = now_eligible
                level_ups.append(LevelUp(
                    job_id=res.job_id, from_level=old_level, to_level=new_level,
                    at_stop_index=stop_index, unlocked=unlocked))

            if pods_left <= 0:
                terminated_reason = "pods_full"
                break

        total_travel = sum(s.travel_cost_from_prev for s in route)
        end_levels = {j: tbl.level_for_xp(job_xp[j]) for j in GATHERING_JOBS}
        levels_gained = {
            j: tbl.level_progress(job_xp[j]) - tbl.level_progress(start_xp[j])
            for j in GATHERING_JOBS if job_xp[j] != start_xp[j]
        }
        total_levels_gained = sum(levels_gained.values())
        objective_total = (100.0 * total_levels_gained) if metric == "levels" else total_xp
        score = objective_total - lam * total_travel
        return RouteResult(
            route=route, total_xp=total_xp, total_travel_cost=total_travel,
            pods_used=pods_limit - pods_left, pods_limit=pods_limit,
            xp_by_job={k: v for k, v in xp_by_job.items() if v > 0},
            score=score, lambda_travel=lam, terminated_reason=terminated_reason,
            level_ups=level_ups, start_levels=start_levels, end_levels=end_levels,
            metric=metric, levels_gained=levels_gained,
            total_levels_gained=total_levels_gained)
