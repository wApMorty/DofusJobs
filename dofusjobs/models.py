"""Domain model for Dofus gathering-job route optimization (v2).

Changes vs v1:
- No fixed Astrub start: the optimizer picks the best starting cell (free travel).
- Spatial model is a graph of MAP CELLS; each cell holds several co-located
  resources. The eligible XP / pods of a cell depend on the player's *current*
  level, which evolves during the pass.
- Player input is current cumulative job XP (not a frozen level); harvesting
  raises XP, may cross a level threshold, and unlocks higher-tier resources
  mid-pass (simulated in the plan).

XP semantics (real Dofus gathering): a resource yields a FIXED ``base_xp`` per
harvest; player level only gates eligibility (``required_level``).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Tuple

# The five gathering (recolte) jobs and their DofusDB job ids.
GATHERING_JOBS: Dict[str, int] = {
    "lumberjack": 2,
    "miner": 24,
    "herbalist": 26,
    "farmer": 28,
    "fisherman": 36,
}

JOB_LABELS_FR: Dict[str, str] = {
    "lumberjack": "Bucheron",
    "miner": "Mineur",
    "herbalist": "Alchimiste",
    "farmer": "Paysan",
    "fisherman": "Pecheur",
}

Coord = Tuple[int, int]


@dataclass(frozen=True)
class Resource:
    """A harvestable resource type (catalog entry, location-independent)."""

    resource_id: str
    name: str
    job_id: str
    base_xp: int
    resource_level: int
    required_level: int
    pods: int

    def is_valid(self) -> bool:
        return (
            self.job_id in GATHERING_JOBS
            and isinstance(self.base_xp, int) and self.base_xp >= 1
            and isinstance(self.required_level, int) and self.required_level >= 1
            and isinstance(self.pods, int) and self.pods >= 1
        )


@dataclass(frozen=True)
class CellResource:
    """A resource present on a cell, with the count harvestable there."""

    resource_id: str
    quantity: int


@dataclass(frozen=True)
class Cell:
    """A map cell: a graph node at world coordinates holding co-located resources."""

    cell_id: str
    world_coords: Coord
    resources: Tuple[CellResource, ...]


@dataclass(frozen=True)
class PlayerInput:
    """User-supplied inputs for a single pass.

    ``job_xp`` is current *cumulative* XP per gathering job (drives the level-up
    simulation). ``lambda_travel`` weights travel (XP per map-screen).
    """

    job_xp: Dict[str, int]
    pods_limit: int
    lambda_travel: float = 1.0
    start_coords: Coord | None = None   # None => optimizer chooses the start
    # Objective metric:
    #   "levels" -> maximize sum of % of a level gained (balances jobs; high-level
    #               resources are worth less because a level costs far more XP).
    #   "xp"     -> maximize raw total XP.
    metric: str = "levels"


@dataclass
class Harvest:
    resource_id: str
    resource_name: str
    job_id: str
    xp: int
    pods: int


@dataclass
class LevelUp:
    """A level threshold crossed during the pass (what makes the path adapt)."""

    job_id: str
    from_level: int
    to_level: int
    at_stop_index: int
    unlocked: List[str] = field(default_factory=list)  # resource ids newly eligible


@dataclass
class RouteStop:
    cell_id: str
    world_coords: Coord
    travel_cost_from_prev: int
    harvests: List[Harvest] = field(default_factory=list)
    # Step-by-step move directions from the previous stop, e.g. ["Est ×2", "Sud"]
    # (empty for the first/free-start stop). Reads better than raw coordinates.
    directions: List[str] = field(default_factory=list)

    @property
    def stop_xp(self) -> int:
        return sum(h.xp for h in self.harvests)

    @property
    def stop_pods(self) -> int:
        return sum(h.pods for h in self.harvests)


@dataclass
class RouteResult:
    route: List[RouteStop]
    total_xp: int
    total_travel_cost: int
    pods_used: int
    pods_limit: int
    xp_by_job: Dict[str, int]
    score: float
    lambda_travel: float
    terminated_reason: str
    level_ups: List[LevelUp] = field(default_factory=list)
    start_levels: Dict[str, int] = field(default_factory=dict)
    end_levels: Dict[str, int] = field(default_factory=dict)
    metric: str = "levels"
    # Fractional levels gained per job (e.g. 1.7 = one level and 70% of the next).
    levels_gained: Dict[str, float] = field(default_factory=dict)
    total_levels_gained: float = 0.0

    def to_dict(self) -> dict:
        return {
            "metric": self.metric,
            "total_xp": self.total_xp,
            "total_levels_gained": round(self.total_levels_gained, 4),
            "levels_gained": {k: round(v, 4) for k, v in self.levels_gained.items()},
            "total_travel_cost": self.total_travel_cost,
            "pods_used": self.pods_used,
            "pods_limit": self.pods_limit,
            "score": round(self.score, 3),
            "lambda_travel": self.lambda_travel,
            "terminated_reason": self.terminated_reason,
            "xp_by_job": self.xp_by_job,
            "start_levels": self.start_levels,
            "end_levels": self.end_levels,
            "level_ups": [
                {
                    "job_id": lu.job_id,
                    "from_level": lu.from_level,
                    "to_level": lu.to_level,
                    "at_stop_index": lu.at_stop_index,
                    "unlocked": lu.unlocked,
                }
                for lu in self.level_ups
            ],
            "route": [
                {
                    "cell_id": s.cell_id,
                    "world_coords": list(s.world_coords),
                    "travel_cost_from_prev": s.travel_cost_from_prev,
                    "directions": s.directions,
                    "stop_xp": s.stop_xp,
                    "stop_pods": s.stop_pods,
                    "harvests": [
                        {
                            "resource_id": h.resource_id,
                            "resource_name": h.resource_name,
                            "job_id": h.job_id,
                            "xp": h.xp,
                            "pods": h.pods,
                        }
                        for h in s.harvests
                    ],
                }
                for s in self.route
            ],
        }
