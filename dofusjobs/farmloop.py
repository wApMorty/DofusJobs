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

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .leveling import JobXpTable
from .mapgraph import MapGraph, path_directions
from .models import GATHERING_JOBS, Cell, Coord, Resource

_REACH = 45          # per-step search radius in screens (keeps each step cheap)
_MAX_STOPS = 600     # safety cap on total route length


class _MctsNode:
    """One node of the MCTS tree: the state reached after walking to and
    harvesting ``coord`` (with every map crossed on the way), plus the search
    statistics. ``path_value``/``path_screens`` are the cumulative %XP gained and
    screens walked from the root to here — the search reward is their ratio (the
    *leveling speed*), not the raw Σvalue−λ·travel (which, with a small λ, an
    unbounded look-ahead would game by wandering far for value at a terrible
    rate). ``r_min``/``r_max`` track the reward span seen in this subtree for the
    per-node min-max normalisation used by UCT."""

    __slots__ = ("coord", "parent", "depth", "pos", "job_xp", "levels",
                 "visited", "path_value", "path_screens", "children", "untried",
                 "n", "w", "r_min", "r_max")

    def __init__(self, coord, parent, depth, pos, job_xp, levels, visited,
                 path_value, path_screens):
        self.coord = coord            # map reached at this node (None for the root)
        self.parent = parent
        self.depth = depth
        self.pos = pos                # position after reaching this node
        self.job_xp = job_xp          # state after harvesting along the way
        self.levels = levels
        self.visited = visited
        self.path_value = path_value      # Σ %XP from root to here
        self.path_screens = path_screens  # Σ screens walked from root to here
        self.children = {}            # coord -> _MctsNode
        self.untried = []             # actions not yet expanded (best-scored first)
        self.n = 0
        self.w = -math.inf            # BEST reward found in this subtree (max-backup)
        self.r_min = math.inf
        self.r_max = -math.inf


def _uct_select(node, c):
    """Pick the child maximising UCT = Q_norm + c·sqrt(ln N_parent / N_child).

    ``Q_norm`` min-max-normalises the child's *best* subtree reward (max-backup —
    this is a deterministic optimisation, so we propagate the best route found,
    not an average that would dilute it) against the reward span seen in this
    parent's subtree, keeping the exploration constant stable across the wildly
    varying %XP scale. Ties broken by coord so selection stays deterministic."""
    ln = math.log(node.n) if node.n > 0 else 0.0
    span = node.r_max - node.r_min
    best = None
    for child in node.children.values():
        qn = (child.w - node.r_min) / span if span > 1e-12 else 0.5
        u = qn + c * math.sqrt(ln / child.n)
        key = (u, child.coord)
        if best is None or key > best[0]:
            best = (key, child)
    return best[1]


@dataclass
class LoopStop:
    world_coords: Coord
    directions: List[str]           # arrows walked since the previous stop
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
            # Walk to the chosen map; every harvestable map crossed on the way
            # becomes its own explicit stop (it's part of the route, not a silent
            # "au passage" pick-up), then the chosen map itself.
            for st in self._leg_stops(pos, c.world_coords, visited, job_xp, cur_levels, metric):
                stops.append(LoopStop(world_coords=tuple(st["coord"]),
                                      directions=st["directions"],
                                      harvests=st["harvests"], value=st["value"]))
            screens += travel
            pos = c.world_coords
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

    def _leg_stops(self, prev, dest, visited, job_xp, cur_levels, metric):
        """Walk the real BFS path ``prev`` -> ``dest`` and turn it into an ordered
        list of **explicit** stops: one per harvestable map crossed on the way
        (these used to be silent "au passage" pick-ups) plus ``dest`` itself.

        Each stop is ``{coord, directions, harvests, value, travel}`` where
        ``directions``/``travel`` cover the screens walked since the previous
        emitted stop (bare pass-through maps are folded into the next stop's
        arrows). Harvests every emitted map: mutates ``visited``/``job_xp``/
        ``cur_levels`` and simulates the level-ups."""
        dest = tuple(dest)
        if prev is None:                       # free start: just the destination
            path = [dest]
            start = 0
        else:
            path = self.graph.shortest_path(tuple(prev), dest) or [tuple(prev), dest]
            start = 1                          # path[0] is where we already are
        stops = []
        anchor = 0                             # index of the last emitted map in path
        for i in range(start, len(path)):
            co = path[i]
            is_dest = i == len(path) - 1
            if co in visited and not is_dest:
                continue
            cell = self.cells_by_coord.get(co)
            if cell is None:
                continue                       # bare map: keep walking, fold its arrow on
            v, _per, items = self._cell_value(cell, cur_levels, job_xp, metric, {})
            if not items and not is_dest:
                continue                       # nothing eligible here at these levels
            stops.append({"coord": list(co), "directions": path_directions(path[anchor:i + 1]),
                          "harvests": items, "value": v, "travel": i - anchor})
            anchor = i
            for it in items:                   # harvest the emitted map; simulate level-ups
                job = it["job_id"]
                job_xp[job] += it["xp"] * it["quantity"]
                lvl = self.xp_table.level_for_xp(job_xp[job])
                if lvl > cur_levels[job]:
                    cur_levels[job] = lvl
            visited.add(co)
        return stops

    # -------------------------------------------------- interactive rolling plan
    def harvest_coord(self, job_xp: Dict[str, int], coord):
        """Apply harvesting the whole map at ``coord`` to ``job_xp`` (returns a
        new dict); the items harvested are whatever is *eligible* at those XP."""
        nx = dict(job_xp)
        cell = self.cells_by_coord.get(tuple(coord))
        if cell:
            levels = {j: self.xp_table.level_for_xp(nx[j]) for j in GATHERING_JOBS}
            for cr in cell.resources:
                r = self.resources[cr.resource_id]
                if levels.get(r.job_id, 0) >= r.required_level:
                    nx[r.job_id] += r.base_xp * cr.quantity
        return nx

    def advance(self, job_xp: Dict[str, int], visited, dest):
        """Commit the next explicit stop in the live planner: harvest the whole
        map at ``dest`` and mark it visited. Each harvestable map is now its own
        stop, so a single advance only ever harvests that one map. Returns the new
        ``(job_xp, visited)``."""
        dest = tuple(dest)
        job_xp = self.harvest_coord(job_xp, dest)
        vis = {tuple(c) for c in visited}
        vis.add(dest)
        return job_xp, [list(c) for c in sorted(vis)]

    def plan_window(self, pos, job_xp: Dict[str, int], visited, horizon: int = 20,
                    metric: str = "levels", lambda_travel: float = 1.0,
                    beam_width: int = 16, branch: int = 5, top_k: int = 150):
        """Beam-search the best next ``horizon`` maps from the given state, for
        the interactive rolling planner. A *beam* of the top ``beam_width``
        partial paths is expanded ``horizon`` steps (each node branches to its
        ``branch`` best next maps), maximising Σ%XP − λ·travel over the window —
        so the path looks ahead instead of being myopically greedy. Only the
        first map is meant to be committed; the rest is a preview, re-planned on
        the next advance.

        For speed the search is restricted to the ``top_k`` richest maps now (at
        the current levels); the beam only ever moves between those, so all the
        single-source BFS distances are reused from cache."""
        metric = "xp" if metric == "xp" else "levels"
        lam = max(0.0, float(lambda_travel))
        base_visited = {tuple(v) for v in visited}
        pos = tuple(pos) if pos else None
        root_levels = {j: self.xp_table.level_for_xp(job_xp[j]) for j in GATHERING_JOBS}

        # candidate universe: the top_k richest un-visited maps right now.
        cand = self._candidate_pool(pos, job_xp, base_visited, root_levels, metric, top_k)

        # node: (path[(coord,items,value,travel)], pos, jx, added(set), val, trav)
        beam = [([], pos, dict(job_xp), set(), 0.0, 0)]
        for _ in range(max(1, int(horizon))):
            cands = []
            for path, p, jx, added, val, trav in beam:
                levels = {j: self.xp_table.level_for_xp(jx[j]) for j in GATHERING_JOBS}
                dmap = self.graph.distances_from(p) if p is not None else None
                scored = []
                for c in cand:
                    co = c.world_coords
                    if co in base_visited or co in added:
                        continue
                    travel = 0 if dmap is None else dmap.get(co)
                    if travel is None:        # different component, unreachable on foot
                        continue
                    v, _p2, items = self._cell_value(c, levels, jx, metric, {})
                    if v <= 0:
                        continue
                    imm = v - lam * travel
                    if imm <= 0:
                        continue
                    scored.append((imm, c, travel, v, items))
                scored.sort(key=lambda x: -x[0])
                for imm, c, travel, v, items in scored[:branch]:
                    njx = dict(jx)
                    for it in items:
                        njx[it["job_id"]] += it["xp"] * it["quantity"]
                    nadded = set(added)
                    nadded.add(c.world_coords)
                    cands.append((path + [(c.world_coords, items, v, travel)],
                                  c.world_coords, njx, nadded, val + v, trav + travel))
            if not cands:
                break
            cands.sort(key=lambda n: -(n[4] - lam * n[5]))
            beam = cands[:beam_width]

        if not beam or not beam[0][0]:
            return []
        best = max(beam, key=lambda n: n[4] - lam * n[5])
        # Materialise the window: each beam leg is flattened into explicit per-map
        # stops (harvestable maps crossed on the way are full steps, not silent
        # pick-ups). Done on local copies of the state (preview — don't mutate the
        # caller's).
        # Each harvestable map is its own explicit step now, so the horizon counts
        # those steps: the beam looks ahead ``horizon`` rich stops (often more maps
        # once flattened) but we only surface the next ``horizon`` of them.
        actions = [co for co, _items, _v, _travel in best[0]]
        return self._materialise_window(actions, pos, job_xp, base_visited, metric, horizon)

    # ----------------------------------- shared planner helpers (beam + MCTS)
    def _candidate_pool(self, pos, job_xp, base_visited, root_levels, metric, top_k):
        """The ``top_k`` richest un-visited maps at the current levels (free start
        => anchored in the richest connected component). Shared candidate universe
        for both the beam and MCTS planners; the search only ever moves between
        these, so single-source BFS distances stay cached and bounded."""
        pool = self.cells
        if pos is None:                       # free start: anchor in richest comp
            comp = {c.world_coords for c in self._richest_component(root_levels, metric)}
            pool = [c for c in self.cells if c.world_coords in comp]
        scored0 = []
        for c in pool:
            if c.world_coords in base_visited:
                continue
            v, _p, _i = self._cell_value(c, root_levels, job_xp, metric, {})
            if v > 0:
                scored0.append((v, c))
        scored0.sort(key=lambda x: -x[0])
        return [c for _v, c in scored0[:top_k]]

    def _materialise_window(self, actions, pos, job_xp, base_visited, metric, horizon):
        """Flatten an ordered list of target-map coords into explicit per-map stops
        (maps crossed on the way are full steps, not silent pick-ups), on local
        copies of the state. Shared output path for the beam and MCTS planners so
        both count crossed maps identically. Truncated to ``horizon`` stops."""
        window = []
        prev = pos
        vis = set(base_visited)
        jx = dict(job_xp)
        levels = {j: self.xp_table.level_for_xp(jx[j]) for j in GATHERING_JOBS}
        for co in actions:
            for st in self._leg_stops(prev, co, vis, jx, levels, metric):
                window.append({"world_coords": st["coord"], "directions": st["directions"],
                               "harvests": st["harvests"], "value": round(st["value"], 4),
                               "travel": st["travel"]})
            prev = co
        return window[:max(1, int(horizon))]

    # -------------------------------------------------- MCTS / UCT rolling plan
    def plan_window_mcts(self, pos, job_xp: Dict[str, int], visited, horizon: int = 20,
                         metric: str = "levels", lambda_travel: float = 1.0,
                         top_k: int = 150, simulations: int = 1500,
                         exploration: float = 0.7, rollout_depth: int = 12,
                         rollout_pool: int = 30, branch: int = 6,
                         screen_budget: int = 35, seed: int = 0):
        """Monte-Carlo Tree Search (UCT) planner — the AlphaZero search family
        *without* a neural net. It looks ahead by simulating, instead of being
        myopically greedy like the beam.

        State = (pos, job_xp, visited); an *action* picks the next map to walk to
        and harvest whole, drawn from the ``top_k`` richest maps frozen at the root
        (same pool as ``plan_window``). Like the beam, the search scores only the
        target map (cached BFS distance + ``_cell_value``); the maps crossed on the
        way are folded in once at the end by ``_materialise_window`` — keeping the
        uncached ``shortest_path`` BFS out of the hot rollout loop.

        UCT balances exploiting high-reward branches against exploring rarely-tried
        ones; the default (rollout) policy is the deterministic greedy of ``find``
        (which still orders candidates by ``%XP - λ·travel``, so ``λ`` shapes route
        density). The *reward*, however, is the route's **leveling speed** — Σ%XP
        gained over screens walked — not the raw Σ%XP-λ·travel: with a small ``λ`` an
        unbounded look-ahead games that sum by wandering far for value at a dreadful
        rate (exactly how a first cut lost to the myopic greedy). Reward is
        min-max-normalised per node so the exploration constant is scale-stable.

        Budget is a fixed number of ``simulations`` (never wall-clock — that would
        break determinism). Output is the principal variation (most-visited / robust
        child), materialised into the same window contract as ``plan_window`` and
        truncated to ``horizon``; only the first stop is meant to be committed, the
        rest is a preview re-planned on the next advance.

        The search is fully deterministic — ties broken by ``(score, -travel,
        coord)`` and budget measured in simulations (never wall-clock) — so repeated
        calls return the same route. ``seed`` is accepted for API symmetry but the
        coord tie-break makes an RNG unnecessary."""
        metric = "xp" if metric == "xp" else "levels"
        lam = max(0.0, float(lambda_travel))
        horizon = max(1, int(horizon))
        pos = tuple(pos) if pos else None
        base_visited = {tuple(v) for v in visited}
        root_levels = {j: self.xp_table.level_for_xp(job_xp[j]) for j in GATHERING_JOBS}
        pool = self._candidate_pool(pos, job_xp, base_visited, root_levels, metric, top_k)
        pool_coords = [c.world_coords for c in pool]
        if not pool_coords:
            return []

        # Hot-loop transition scores only the TARGET map (cached BFS distance +
        # ``_cell_value``), exactly like the beam search: harvesting the maps
        # crossed on the way is deferred to ``_materialise_window`` at the end.
        # This keeps ``shortest_path`` (an *uncached* BFS) out of the rollout,
        # which is the difference between ~1 s and tens of seconds.

        # Rollouts greedily walk only the richest ``rollout_pool`` maps (the pool is
        # value-sorted): a cheaper leaf estimate than scanning the whole tree pool.
        roll_coords = pool_coords[:max(1, int(rollout_pool))]

        # --- best single action from a state (greedy rollout pick) --------------
        def pick_best(p, jx, lv, vis):
            dmap = self.graph.distances_from(p) if p is not None else None
            best = None                       # (key, co, v, travel, items)
            for co in roll_coords:
                if co in vis:
                    continue
                travel = 0 if dmap is None else dmap.get(co)
                if travel is None:            # different component, unreachable on foot
                    continue
                v, _p, items = self._cell_value(self.cells_by_coord[co], lv, jx, metric, {})
                if v <= 0:
                    continue
                score = v - lam * travel
                if score <= 0:
                    continue
                key = (score, -travel, co)
                if best is None or key > best[0]:
                    best = (key, co, v, travel, items)
            return best

        # --- all reachable positive-score actions, best-first ------------------
        def evaluate(p, jx, lv, vis):
            dmap = self.graph.distances_from(p) if p is not None else None
            scored = []
            for co in pool_coords:
                if co in vis:
                    continue
                travel = 0 if dmap is None else dmap.get(co)
                if travel is None:
                    continue
                v, _p, items = self._cell_value(self.cells_by_coord[co], lv, jx, metric, {})
                if v <= 0:
                    continue
                sc = v - lam * travel
                if sc <= 0:
                    continue
                scored.append((sc, -travel, co, v, travel, items))
            scored.sort(key=lambda t: (t[0], t[1], t[2]), reverse=True)
            # Only ever branch to the B best-scored actions: with ~150 candidates a
            # fixed simulation budget would spread far too thin to tell them apart
            # (the failure mode that made an unfocused first cut pick near-randomly).
            # Top-B by ``%XP-λ·travel`` is the same strong prior the beam's ``branch``
            # uses; the tree then deepens within it.
            return scored[:max(1, int(branch))]

        # --- harvest the target map into the passed copies (mutates) -----------
        def apply_target(jx, lv, vis, co, items):
            for it in items:
                j = it["job_id"]
                jx[j] += it["xp"] * it["quantity"]
                lvl = self.xp_table.level_for_xp(jx[j])
                if lvl > lv[j]:
                    lv[j] = lvl
            vis.add(co)

        # --- deterministic greedy rollout from a node, to a fixed SCREEN budget --
        # Reward is "value gained within ``budget`` screens": every simulation is
        # measured over the same travel budget, so a route that wanders far burns
        # the budget on fewer maps and scores less — which is exactly leveling
        # *speed*, posed as budgeted orienteering (maximise value at fixed cost)
        # rather than a raw ratio (which a short rollout estimates badly, dropping
        # a big-value-but-slightly-distant first map for a poor dense one).
        rcap = max(1, int(rollout_depth))
        budget = max(1, int(screen_budget))

        def rollout(p, jx, lv, vis, screens0):
            jx = dict(jx); lv = dict(lv); vis = set(vis)
            value = 0.0
            screens = screens0
            steps = 0
            while screens < budget and steps < rcap:
                best = pick_best(p, jx, lv, vis)
                if best is None:
                    break
                _key, co, v, travel, items = best
                apply_target(jx, lv, vis, co, items)
                value += v
                screens += travel
                p = co
                steps += 1
            return value, screens - screens0

        root = _MctsNode(None, None, 0, pos, dict(job_xp), dict(root_levels),
                         set(base_visited), 0.0, 0)
        root.untried = evaluate(root.pos, root.job_xp, root.levels, root.visited)

        for _ in range(max(1, int(simulations))):
            node = root
            # selection: descend fully-expanded nodes by UCT until a frontier node
            while not node.untried and node.children and node.depth < horizon:
                node = _uct_select(node, exploration)
            # expansion: open one untried action (best-scored first)
            if node.untried and node.depth < horizon:
                _sc, _nt, co, v, travel, items = node.untried.pop(0)
                jx = dict(node.job_xp); lv = dict(node.levels); vis = set(node.visited)
                apply_target(jx, lv, vis, co, items)
                child = _MctsNode(co, node, node.depth + 1, co, jx, lv, vis,
                                  node.path_value + v, node.path_screens + travel)
                child.untried = (evaluate(co, jx, lv, vis)
                                 if child.depth < horizon and child.path_screens < budget
                                 else [])
                node.children[co] = child
                node = child
            # simulation: leveling value gained within the screen budget, net of the
            # user's travel weight. The budget bounds the route (so a small λ can't
            # be gamed by wandering for value), while the −λ·screens term keeps a
            # travel-averse (large λ) user on short dense routes rather than always
            # spending the whole budget.
            rv, rs = rollout(node.pos, node.job_xp, node.levels, node.visited,
                             node.path_screens)
            reward = (node.path_value + rv) - lam * (node.path_screens + rs)
            # backpropagation: keep the BEST reward seen (max-backup) + the local
            # min-max span, along the path to the root.
            while node is not None:
                node.n += 1
                if reward > node.w:
                    node.w = reward
                if reward < node.r_min:
                    node.r_min = reward
                if reward > node.r_max:
                    node.r_max = reward
                node = node.parent

        # principal variation: descend by best-valued child (the first step of the
        # best route found — which, since every child was at least greedily rolled
        # out, is never worse than the myopic greedy choice).
        actions = []
        node = root
        while node.children and len(actions) < horizon:
            node = max(node.children.values(), key=lambda c: (c.w, c.coord))
            actions.append(node.coord)
        return self._materialise_window(actions, pos, job_xp, base_visited, metric, horizon)
