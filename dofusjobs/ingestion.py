"""Load the real DofusJobs dataset built from DofusDB (see scripts/build_dofusdb_dataset.py).

Files (data/):
  resources.json  : gathering resources (real DofusDB level/pods, calibrated XP).
  world_cells.json: per-map cells with resources (sub-area counts spread over maps).
  world_maps.json : real main-world map coordinates (graph nodes).

Rows failing the data contract are dropped and logged to stderr.
"""

from __future__ import annotations

import json
import os
import sys
from typing import Dict, List, Tuple

from .models import GATHERING_JOBS, Cell, CellResource, Coord, Resource

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")


def _log(msg: str) -> None:
    print(f"[ingestion] {msg}", file=sys.stderr)


def _load(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def load_resources(path: str | None = None) -> Dict[str, Resource]:
    raw = _load(path or os.path.join(DATA_DIR, "resources.json"))
    out: Dict[str, Resource] = {}
    for row in raw.get("resources", []):
        try:
            res = Resource(
                resource_id=str(row["resource_id"]), name=str(row["name"]),
                job_id=str(row["job"]), base_xp=int(row["base_xp"]),
                resource_level=int(row["resource_level"]),
                required_level=int(row["required_level"]), pods=int(row["pods"]))
        except (KeyError, TypeError, ValueError) as exc:
            _log(f"dropped resource row (parse error: {exc}): {row!r}")
            continue
        if not res.is_valid():
            _log(f"dropped resource '{res.resource_id}' failing data contract")
            continue
        out[res.resource_id] = res
    return out


def load_cells(resources: Dict[str, Resource], path: str | None = None) -> List[Cell]:
    raw = _load(path or os.path.join(DATA_DIR, "world_cells.json"))
    out: List[Cell] = []
    dropped_res = 0
    for row in raw.get("cells", []):
        try:
            x, y = (int(v) for v in row["world_coords"])
            cid = str(row["cell_id"])
        except (KeyError, TypeError, ValueError) as exc:
            _log(f"dropped cell row (parse error: {exc})")
            continue
        kept = []
        for cr in row.get("resources", []):
            rid = str(cr.get("resource_id"))
            if rid not in resources:
                dropped_res += 1
                continue
            try:
                qty = int(cr["quantity"])
            except (KeyError, TypeError, ValueError):
                continue
            if qty > 0:
                kept.append(CellResource(resource_id=rid, quantity=qty))
        if kept:
            out.append(Cell(cell_id=cid, world_coords=(x, y), resources=tuple(kept)))
    if dropped_res:
        _log(f"dropped {dropped_res} cell-resources referencing unknown resources")
    return out


def load_maps(path: str | None = None) -> List[Coord]:
    raw = _load(path or os.path.join(DATA_DIR, "world_maps.json"))
    return [(int(x), int(y)) for x, y in raw.get("maps", [])]


def load_dataset() -> Tuple[Dict[str, Resource], List[Cell], List[Coord]]:
    resources = load_resources()
    cells = load_cells(resources)
    maps = load_maps()
    by_job: Dict[str, int] = {}
    for r in resources.values():
        by_job[r.job_id] = by_job.get(r.job_id, 0) + 1
    _log(f"loaded {len(resources)} resources, {len(cells)} cells, {len(maps)} maps "
         f"({', '.join(f'{j}:{by_job.get(j,0)}' for j in GATHERING_JOBS)})")
    return resources, cells, maps
