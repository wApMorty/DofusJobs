"""DofusJobs: optimized gathering-job leveling routes for Dofus (Unity), v4.

Free start (no fixed Astrub), cell-based map graph, and in-plan level-up
simulation (unlocking higher-tier resources mid-pass).

Public API:
    load_dataset, MapGraph, JobXpTable, FarmLoopFinder, plan_farm_route,
    resolve_engine, GATHERING_JOBS, JOB_LABELS_FR
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
    Resource,
)
from .farmloop import AVAILABILITY_ALPHA, FarmLoopFinder, FarmLoopResult, ewma_update
from .engine_policy import load_policy, resolve_engine

__all__ = [
    "load_dataset",
    "MapGraph",
    "FarmLoopFinder",
    "FarmLoopResult",
    "ewma_update",
    "AVAILABILITY_ALPHA",
    "JobXpTable",
    "Resource",
    "Cell",
    "CellResource",
    "GATHERING_JOBS",
    "JOB_LABELS_FR",
    "plan_farm_route",
    "resolve_engine",
    "load_policy",
]

__version__ = "0.2.0"


def plan_farm_route(job_levels: Optional[Dict[str, int]] = None,
                    *,
                    metric: str = "levels",
                    lambda_travel: float = 1.0,
                    resources: Optional[Dict[str, Resource]] = None,
                    cells: Optional[List[Cell]] = None,
                    maps: Optional[List[Tuple[int, int]]] = None,
                    xp_table: Optional[JobXpTable] = None) -> FarmLoopResult:
    """Greedy leveling route (v4, no pods): score every map by the total %XP it
    yields at the current levels and walk the best score-per-screen path,
    harvesting whole maps and simulating level-ups, to level all jobs fastest.
    ``lambda_travel`` = %XP traded per screen (higher = shorter, denser route)."""
    if resources is None or cells is None or maps is None:
        resources, cells, maps = load_dataset()
    xp_table = xp_table or JobXpTable.load()
    levels = {j: int((job_levels or {}).get(j, 1)) for j in GATHERING_JOBS}
    finder = FarmLoopFinder(resources, cells, maps=maps, xp_table=xp_table)
    return finder.find(levels, metric=metric, lambda_travel=float(lambda_travel))
