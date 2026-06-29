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

from dataclasses import dataclass
from typing import Dict, Tuple

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
    required_level: int

    def is_valid(self) -> bool:
        return (
            self.job_id in GATHERING_JOBS
            and isinstance(self.base_xp, int) and self.base_xp >= 1
            and isinstance(self.required_level, int) and self.required_level >= 1
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
