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
from collections import defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")

# dofus-map per-map counts are mostly realistic spot counts (median 1, p90 3,
# 97% <= 8) but a rare hub tail (a few maps aggregate a whole zone, up to ~105)
# would let the optimizer dump its whole pods budget on a single screen and
# collapse the tour. Clip that tail so location precision is kept while per-map
# harvesting stays realistic; 0 disables the cap.
PER_MAP_CAP = 10
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


WOOD_PREFIXES = ("bois de ", "bois d'", "bois du ")


def dofusmap_keys(name):
    """Candidate dofus-map name-slugs for a DofusDB catalog resource name.

    dofus-map names the raw resource ("Frêne") while DofusDB names the gathered
    item ("Bois de Frêne"); fish add a qualifier ("Crabe" -> "Crabe Sourimi").
    Tried most-specific first; first hit in dofusmap_counts.json wins.
    """
    low = name.lower()
    cands = [slug(name)]
    for p in WOOD_PREFIXES:
        if low.startswith(p):
            cands.append(slug(name[len(p):]))      # "Bois de Frêne" -> "frene"
    first = slug(name.split()[0])                  # "Crabe Sourimi" -> "crabe"
    if first not in cands:
        cands.append(first)
    return cands


def load_dofusmap_counts():
    """{dm_slug: {(x,y): count}} from data/dofusmap_counts.json, or {} if absent."""
    path = os.path.join(DATA, "dofusmap_counts.json")
    if not os.path.exists(path):
        return {}
    raw = json.load(open(path, encoding="utf-8"))
    out = {}
    for sl, cells in raw.items():
        d = {}
        for key, c in cells.items():
            x, y = key.split(",")
            d[(int(x), int(y))] = c
        out[sl] = d
    return out


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
    """All worldMap=1 map coords + subAreaId -> set of coords."""
    coords = set()
    sub2coords = defaultdict(set)
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
            coords.add((x, y))
            if sa is not None:
                sub2coords[sa].add((x, y))
        skip += 50
        if skip % 2000 == 0:
            print(f"  map-positions {skip}/{total}")
        if skip >= total:
            break
        time.sleep(0.02)
    return coords, sub2coords


def main():
    print("1) resources from DofusDB ...")
    resources = build_resources()
    print(f"   harvestable gathering resources: {len(resources)}")

    print("2) world maps (worldMap=1) ...")
    coords, sub2coords = fetch_world_maps()
    print(f"   main-world maps: {len(coords)}, sub-areas with maps: {len(sub2coords)}")

    print("3) building cells (dofus-map real per-map counts; sub-area spread fallback) ...")
    dm_counts = load_dofusmap_counts()
    if dm_counts:
        print(f"   dofus-map counts loaded for {len(dm_counts)} resources")
    cell_res = defaultdict(dict)   # (x,y) -> {rid: qty}
    n_real = n_fallback = 0
    for rid, meta in resources.items():
        # Prefer real dofus-map case-level counts, kept only for coords that are
        # real worldMap=1 maps (our graph nodes). Off-world spawns are dropped.
        real = None
        for key in dofusmap_keys(meta["name"]):
            if key in dm_counts:
                real = {c: q for c, q in dm_counts[key].items() if c in coords}
                break
        if real:                       # at least one in-world spawn -> use real data
            n_real += 1
            for c, q in real.items():
                if PER_MAP_CAP:
                    q = min(q, PER_MAP_CAP)
                cell_res[c][rid] = cell_res[c].get(rid, 0) + q
        else:                          # no dofus-map data (or all off-world): spread
            n_fallback += 1
            for sa, count in meta["subareas"]:
                maps = sub2coords.get(sa)
                if not maps:
                    continue
                per = max(1, round(count / len(maps)))
                for c in maps:
                    cell_res[c][rid] = cell_res[c].get(rid, 0) + per
    print(f"   resources placed: {n_real} from dofus-map, {n_fallback} via sub-area spread")
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
