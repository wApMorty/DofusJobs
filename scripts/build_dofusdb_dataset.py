#!/usr/bin/env python3
"""Build the real DofusJobs dataset from the official DofusDB API.

Outputs (data/):
  resources.json  : harvestable gathering resources — resource_id, name, job,
                    required_level (item.level), pods (item.realWeight), base_xp
                    (community-calibrated), and resourcesBySubarea counts.
  world_maps.json : every real map coordinate of the main world (worldMap=1) ->
                    the graph nodes (used for real A* walking distance).
  world_cells.json: per-map cells — each map coord that bears resources, with
                    [{resource_id, quantity}]. A sub-area's resource count is
                    spread across that sub-area's maps (no finer data exists).

All data is authoritative DofusDB except base_xp (community-calibrated:
xp ≈ 7 + 0.36*level, anchored on next-stage wood values) — DofusDB has no
harvest XP.
"""
from __future__ import annotations
import json, os, re, time, urllib.request
from collections import Counter, defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")

UA = {"User-Agent": "DofusJobs/1.0 (gathering-route optimizer; official API)"}

TYPE_JOB = {34: "farmer", 35: "herbalist", 36: "herbalist", 38: "lumberjack",
            39: "miner", 41: "fisherman", 49: "fisherman"}


def get(url, timeout=25):
    return json.loads(urllib.request.urlopen(
        urllib.request.Request(url, headers=UA), timeout=timeout).read().decode("utf-8", "ignore"))


def slug(name):
    s = name.lower().strip()
    for a, b in (("é", "e"), ("è", "e"), ("ê", "e"), ("à", "a"), ("â", "a"), ("î", "i"),
                 ("ï", "i"), ("ô", "o"), ("û", "u"), ("ç", "c"), ("œ", "oe"),
                 ("'", "_"), ("-", "_"), (" ", "_")):
        s = s.replace(a, b)
    return re.sub(r"[^a-z0-9_]", "", s)


def harvest_xp(level):
    # Community-calibrated (next-stage wood midpoints): linear in resource level.
    return max(1, round(7 + 0.36 * level))


def build_resources():
    resources = {}
    for tid, job in TYPE_JOB.items():
        skip = 0
        while True:
            d = get(f"https://api.dofusdb.fr/items?typeId={tid}&lang=fr&$limit=50&$skip={skip}")
            for it in d.get("data", []):
                rbs = it.get("resourcesBySubarea") or []
                if not rbs:            # only items that actually spawn in the world
                    continue
                nm = (it.get("name") or {}).get("fr")
                lvl = it.get("level")
                pods = it.get("realWeight")
                if not nm or not isinstance(lvl, int) or not isinstance(pods, int) or pods < 1:
                    continue
                rid = slug(nm)
                resources[rid] = {
                    "resource_id": rid, "name": nm, "job": job,
                    "required_level": max(1, lvl), "resource_level": max(1, lvl),
                    "pods": pods, "base_xp": harvest_xp(lvl),
                    "subareas": [[int(s[0]), int(s[1])] for s in rbs if len(s) == 2],
                }
            skip += 50
            if skip >= d.get("total", 0):
                break
            time.sleep(0.03)
    return resources


def fetch_world_maps():
    """worldMap=1 coords (set) per sub-area + #worldMap=1 map-positions per
    sub-area (the surface share, for apportioning the sub-area's count)."""
    coords = set()
    sub2coords = defaultdict(set)
    sa_wm1 = Counter()          # worldMap=1 map-positions per sub-area
    pos_count = Counter()
    skip = 0
    total = None
    while True:
        d = get(f"https://api.dofusdb.fr/map-positions?worldMap=1&$limit=50&$skip={skip}")
        total = d.get("total")
        rows = d.get("data", [])
        if not rows:
            break
        for r in rows:
            x, y, sa = r.get("posX"), r.get("posY"), r.get("subAreaId")
            if x is None or y is None:
                continue
            if x == 0 and y == 0:        # DofusDB's null-island: maps with no real
                continue                 # coords are dumped at (0,0) (1779 of them,
                # across 13 sub-areas) -> a fake multi-resource hub. Drop it.
            pos_count[(x, y)] += 1
            coords.add((x, y))
            if sa is not None:
                sub2coords[sa].add((x, y))
                sa_wm1[sa] += 1
        skip += 50
        if skip % 2000 == 0:
            print(f"  map-positions {skip}/{total}")
        if skip >= total:
            break
        time.sleep(0.02)
    busy = [(c, n) for c, n in pos_count.most_common(5) if n > 20]
    if busy:
        print(f"   busiest coords (possible placeholders): {busy}")
    return coords, sub2coords, sa_wm1


def fetch_subarea_total_maps(sas):
    """Total map-positions (ALL worldMaps) per sub-area — the denominator for
    apportioning a sub-area's resource count to its surface (worldMap=1) share."""
    out = {}
    sas = sorted(sas)
    for i, sa in enumerate(sas):
        try:
            out[sa] = get(f"https://api.dofusdb.fr/map-positions?subAreaId={sa}&$limit=0").get("total") or 0
        except Exception:                                  # noqa: BLE001
            out[sa] = 0
        if i % 50 == 0:
            print(f"  sub-area totals {i}/{len(sas)}")
        time.sleep(0.02)
    return out


def main():
    print("1) resources from DofusDB ...")
    resources = build_resources()
    print(f"   harvestable gathering resources: {len(resources)}")

    print("2) world maps (worldMap=1) ...")
    coords, sub2coords, sa_wm1 = fetch_world_maps()
    print(f"   main-world maps: {len(coords)}, sub-areas with maps: {len(sub2coords)}")

    print("2b) sub-area total map counts (apportion to surface share) ...")
    sa_total = fetch_subarea_total_maps(sub2coords.keys())

    print("3) building cells (authoritative DofusDB counts, worldMap=1 surface only) ...")
    cell_res = defaultdict(dict)   # (x,y) -> {rid: qty}
    n_placed = n_absent = 0
    for rid, meta in resources.items():
        # MAGNITUDE = DofusDB's authoritative count per sub-area, spread over that
        # sub-area's maps. `sub2coords` holds ONLY worldMap=1 maps, so interior
        # sub-areas (worldMap=-1, where most fish/ore actually lives) and other
        # continents (worldMap=3...) are excluded by construction — that removes the
        # "hubs" at the source. (We tried dofus-map's per-coord counts for finer
        # placement, but its groupId=0 merges every worldmap and projects interior
        # spawns onto the surface entrance coord — inflated and unreliable, so the
        # real surface signal is DofusDB's worldMap=1 sub-area count, no thresholds.)
        # A surface coord can sit in several overlapping sub-areas (a mine entrance
        # is both on the mountain and in the mine zone). Give the resource to each
        # coord from only ONE of its sub-areas (dedup) so it isn't summed into a
        # fake hub (e.g. iron at the Mine Istairameur entrance).
        placed = False
        seen = set()
        for sa, count in sorted(meta["subareas"]):
            maps = sub2coords.get(sa)
            if not maps or count <= 0:
                continue
            # Apportion the sub-area's count to its SURFACE share: a mine sub-area
            # may hold 121 iron but be mostly interior (worldMap=-1) with only a few
            # worldMap=1 entrance maps — only that fraction is harvestable on the
            # overworld. count_surface = count * (worldMap1 positions / all positions).
            total_pos = sa_total.get(sa) or sa_wm1.get(sa, len(maps))
            count = round(count * sa_wm1.get(sa, len(maps)) / total_pos) if total_pos else count
            if count <= 0:
                continue
            maps = sorted(maps)
            # density = count over ALL of the sub-area's maps (low), but only place
            # on coords not already given this resource by an earlier sub-area —
            # the overlap share is dropped, not piled up into a hub.
            base, extra = divmod(count, len(maps))
            for i, c in enumerate(maps):
                if c in seen:
                    continue
                q = base + (1 if i < extra else 0)
                if q:
                    cell_res[c][rid] = cell_res[c].get(rid, 0) + q
                    placed = True
            seen.update(maps)
        n_placed += placed
        n_absent += not placed
    print(f"   resources on the surface: {n_placed}; off-surface only (interior/other worldmap): {n_absent}")
    cells = [{"cell_id": f"{x},{y}", "world_coords": [x, y],
              "resources": [{"resource_id": r, "quantity": q} for r, q in sorted(rs.items())]}
             for (x, y), rs in sorted(cell_res.items())]
    print(f"   resource-bearing cells: {len(cells)}")

    # Strip subareas from the saved catalog (keep it lean).
    cat = {rid: {k: v for k, v in m.items() if k != "subareas"} for rid, m in resources.items()}
    json.dump({"resources": list(cat.values())}, open(os.path.join(DATA, "resources.json"), "w"), ensure_ascii=False, indent=0)
    json.dump({"maps": [[x, y] for (x, y) in sorted(coords)]}, open(os.path.join(DATA, "world_maps.json"), "w"))
    json.dump({"cells": cells}, open(os.path.join(DATA, "world_cells.json"), "w"), ensure_ascii=False)
    print(f"\nDONE. resources={len(cat)} maps={len(coords)} cells={len(cells)}")


if __name__ == "__main__":
    main()
