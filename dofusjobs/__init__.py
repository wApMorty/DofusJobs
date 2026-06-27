"""DofusJobs: optimized gathering-job leveling routes for Dofus (Unity), v2.

Free start (no fixed Astrub), cell-based map graph, and in-plan level-up
simulation (unlocking higher-tier resources mid-pass).

Public API:
    load_dataset, Optimizer, MapGraph, JobXpTable, PlayerInput, RouteResult,
    plan_route, GATHERING_JOBS, JOB_LABELS_FR
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from .ingestion import load_dataset
from .leveling import JobXpTable
from .mapgraph import MapGraph
from .models import (
    GATHERING_JOBS,
    JOB_LABELS_FR,
    Cell,
    CellResource,
    PlayerInput,
    Resource,
    RouteResult,
)
from .optimizer import Optimizer

__all__ = [
    "load_dataset",
    "MapGraph",
    "Optimizer",
    "JobXpTable",
    "PlayerInput",
    "Resource",
    "Cell",
    "CellResource",
    "RouteResult",
    "GATHERING_JOBS",
    "JOB_LABELS_FR",
    "plan_route",
]

__version__ = "0.2.0"


def plan_route(job_xp: Optional[Dict[str, int]] = None,
               *,
               job_levels: Optional[Dict[str, int]] = None,
               pods_limit: int,
               lambda_travel: float = 1.0,
               metric: str = "levels",
               start_coords: Optional[Tuple[int, int]] = None,
               resources: Optional[Dict[str, Resource]] = None,
               cells: Optional[List[Cell]] = None,
               maps: Optional[List[Tuple[int, int]]] = None,
               xp_table: Optional[JobXpTable] = None) -> RouteResult:
    """Compute the optimized route.

    Provide either ``job_xp`` (cumulative XP per job) or ``job_levels`` (mapped
    to the XP at the start of that level). ``metric`` is "levels" (maximize % of
    a level gained — balances jobs) or "xp" (maximize raw XP). ``start_coords=None``
    lets the optimizer choose the start (free travel).
    """
    if resources is None or cells is None or maps is None:
        resources, cells, maps = load_dataset()
    xp_table = xp_table or JobXpTable.load()

    if job_xp is None:
        levels = job_levels or {}
        job_xp = {j: xp_table.xp_for_level(int(levels.get(j, 1))) for j in GATHERING_JOBS}
    xp = {j: int(job_xp.get(j, 0)) for j in GATHERING_JOBS}

    player = PlayerInput(job_xp=xp, pods_limit=int(pods_limit),
                         lambda_travel=float(lambda_travel), start_coords=start_coords,
                         metric=metric)
    return Optimizer(resources, cells, maps=maps, xp_table=xp_table).optimize(player)
