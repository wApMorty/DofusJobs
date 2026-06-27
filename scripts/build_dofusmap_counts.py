#!/usr/bin/env python3
"""Crawl real per-map resource counts from dofus-map.com (groupId=0).

`getRessourceData.php?ressourceId=<R>&groupId=0` returns the WHOLE-WORLD
distribution of one resource in a single request, at true Dofus world
coordinates (the same x:y system as DofusDB worldMap=1 map-positions). So a
complete case-level cartography of every gathering resource is ~80 polite
requests (one per resource), cached on disk.

Response shape:  "<R>&0&<body>"  (surrounding quotes included)
  body  = groups separated by '_'
  group = count*<spec>+<spec>+...   (count applies to every coord in the group)
  spec  = x:y1 y2 y3 ...            (one x, then 1+ y values space-separated)
So `4*0:9 -21+3:22 31` = count 4 at (0,9),(0,-21),(3,22),(3,31).

Output: data/dofusmap_counts.json = { dm_slug: { "x,y": count, ... }, ... }
keyed by slug(dofus-map resource name). build_dofusdb_dataset.py bridges these
slugs to the DofusDB catalog (handles "Bois de Frêne"<->"Frêne", etc.).

Raw responses are cached under data/cache/dofusmap/<R>.txt so re-runs are free.
"""
from __future__ import annotations
import json, os, re, sys, time, urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
CACHE = os.path.join(DATA, "cache", "dofusmap")
IDS_FILE = os.path.join(ROOT, "notes", "dofusmap_resource_ids.json")
UA = {"User-Agent": "Mozilla/5.0 (DofusJobs gathering-route optimizer; research)"}


def slug(name):
    s = name.lower().strip()
    for a, b in (("é", "e"), ("è", "e"), ("ê", "e"), ("à", "a"), ("â", "a"), ("î", "i"),
                 ("ï", "i"), ("ô", "o"), ("û", "u"), ("ç", "c"), ("œ", "oe"),
                 ("'", "_"), ("-", "_"), (" ", "_")):
        s = s.replace(a, b)
    return re.sub(r"[^a-z0-9_]", "", s)


def fetch_raw(rid, refresh=False):
    """Return the raw response for a dofus-map resource id, cached on disk."""
    os.makedirs(CACHE, exist_ok=True)
    path = os.path.join(CACHE, f"{rid}.txt")
    if os.path.exists(path) and not refresh:
        with open(path, encoding="utf-8") as f:
            return f.read()
    url = f"https://dofus-map.com/getRessourceData.php?ressourceId={rid}&groupId=0"
    raw = urllib.request.urlopen(
        urllib.request.Request(url, headers=UA), timeout=30).read().decode("utf-8", "ignore")
    with open(path, "w", encoding="utf-8") as f:
        f.write(raw)
    time.sleep(0.4)  # be polite
    return raw


def decode(raw):
    """Decode a raw "<R>&0&<body>" response into {(x, y): count}.

    Tolerant of the surrounding quotes and of empty bodies.
    """
    body = raw.strip().strip('"')
    _, _, body = body.partition("&0&")
    body = body.strip().strip('"')
    out = {}
    if not body:
        return out
    for group in body.split("_"):
        if "*" not in group:
            continue
        cnt_s, _, specs = group.partition("*")
        try:
            count = int(cnt_s)
        except ValueError:
            continue
        for spec in specs.split("+"):
            if ":" not in spec:
                continue
            x_s, _, ys = spec.partition(":")
            try:
                x = int(x_s)
            except ValueError:
                continue
            for y_s in ys.split():        # space-separated y values share this x
                try:
                    y = int(y_s)
                except ValueError:
                    continue
                out[(x, y)] = out.get((x, y), 0) + count
    return out


def main(argv):
    refresh = "--refresh" in argv
    ids = json.load(open(IDS_FILE, encoding="utf-8"))

    # optional: world maps for an overlap sanity check (coord-system validation)
    world = None
    wm_path = os.path.join(DATA, "world_maps.json")
    if os.path.exists(wm_path):
        world = {(x, y) for x, y in json.load(open(wm_path))["maps"]}

    counts = {}        # dm_slug -> {"x,y": count}
    report = []        # (id, name, n_coords, total_units, overlap_pct)
    print(f"Crawling {len(ids)} resources from dofus-map (groupId=0, cached)...")
    for rid, name in sorted(ids.items(), key=lambda kv: int(kv[0])):
        try:
            raw = fetch_raw(rid, refresh=refresh)
        except Exception as e:                       # noqa: BLE001
            print(f"  !! {rid} {name}: fetch failed: {e}", file=sys.stderr)
            continue
        cells = decode(raw)
        sl = slug(name)
        counts[sl] = {f"{x},{y}": c for (x, y), c in sorted(cells.items())}
        total = sum(cells.values())
        ov = ""
        if world is not None and cells:
            inside = sum(1 for c in cells if c in world)
            ov = f"{100 * inside // len(cells):3d}% in-world"
        report.append((int(rid), name, len(cells), total))
        print(f"  {int(rid):>3} {name:<22} {len(cells):>4} maps  {total:>5} units  {ov}")

    out = os.path.join(DATA, "dofusmap_counts.json")
    json.dump(counts, open(out, "w", encoding="utf-8"), ensure_ascii=False)
    nz = sum(1 for v in counts.values() if v)
    print(f"\nDONE. {nz}/{len(counts)} resources with >=1 map -> {out}")


if __name__ == "__main__":
    main(sys.argv[1:])
