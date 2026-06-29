"""Zero-dependency web app for DofusJobs (stdlib http.server only).

Routes:
  GET  /                 -> input form (level per job, lambda, metric)
  POST /api/plan         -> JSON {job_levels|state, horizon, lambda_travel, metric,
                            engine: "beam"(default)|"mcts"}
                            -> next window of the interactive rolling planner
  GET  /healthz          -> "ok"

Run:  python -m webapp.app  [--port 8000]
"""

from __future__ import annotations

import argparse
import html
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dofusjobs import (  # noqa: E402
    GATHERING_JOBS,
    JOB_LABELS_FR,
    FarmLoopFinder,
    JobXpTable,
    ewma_update,
    load_dataset,
    resolve_engine,
)

TEMPLATE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "templates")

# Loaded once at startup.
_DATA = {"resources": None, "cells": None, "maps": None}


def get_data():
    if _DATA["resources"] is None:
        resources, cells, maps = load_dataset()
        _DATA.update(resources=resources, cells=cells, maps=maps)
    return _DATA["resources"], _DATA["cells"], _DATA["maps"]


def render_index() -> str:
    with open(os.path.join(TEMPLATE_DIR, "index.html"), encoding="utf-8") as fh:
        tpl = fh.read()
    rows = "\n".join(
        f'<div class="jobrow">'
        f'<label><input type="checkbox" name="sel_{j}" checked '
        f'title="Décocher pour ignorer ce métier"> {html.escape(JOB_LABELS_FR[j])}</label>'
        f'<input type="number" min="0" max="200" name="lvl_{j}" value="1"></div>'
        for j in GATHERING_JOBS
    )
    return tpl.replace("{{JOB_ROWS}}", rows)


_XP_TABLE = JobXpTable.load()


_FINDER = {"obj": None}


def _finder() -> FarmLoopFinder:
    """A single shared finder, reused across requests. The dataset and travel
    graph are immutable and the graph's BFS results are pure memoisation, so
    sharing one instance warms the distance cache across calls (a big speed-up
    for the rollout-heavy MCTS engine) and is safe under the GIL (the caches are
    append-only)."""
    if _FINDER["obj"] is None:
        resources, cells, maps = get_data()
        _FINDER["obj"] = FarmLoopFinder(resources, cells, maps=maps, xp_table=_XP_TABLE)
    return _FINDER["obj"]


def _ckey(coord) -> str:
    """Canonical "x,y" string key for the client-side availability dict."""
    return f"{int(coord[0])},{int(coord[1])}"


def _avail_to_coords(avail: dict) -> dict:
    """Turn the client's {"x,y": a} availability map into the engine's
    {(x, y): a} form, dropping any malformed key (=> that map keeps a=1.0)."""
    out = {}
    for k, v in (avail or {}).items():
        try:
            xs, ys = str(k).split(",")
            out[(int(xs), int(ys))] = float(v)
        except (ValueError, AttributeError):
            continue
    return out


def plan(payload: dict) -> dict:
    """Interactive rolling planner: returns the next ``horizon`` maps from the
    current state, re-planned each call (beam lookahead). The client passes the
    opaque ``state`` back, plus an optional ``commit`` (the map it just did,
    skipped, or reported empty); the server applies it and re-plans.

    ``state.availability`` is the learned {"x,y": a} bot-depletion map (a in (0,1],
    default 1.0): an ``advance`` commit harvested the map (observation 1, a recovers),
    an ``empty`` commit reported it botted (observation 0, a decays by x0.8 and the
    map is NOT advanced onto), a ``skip`` is "not now" and leaves a untouched. The
    discount only lowers a map's routing value, never the travel cost, so a low-a
    map already on the path is still harvested for free."""
    f = _finder()
    horizon = max(1, min(40, int(payload.get("horizon", 20))))
    lam = float(payload.get("lambda_travel", 1.0))
    metric = "xp" if payload.get("metric") == "xp" else "levels"
    raw_engine = payload.get("engine")
    # Selected jobs to level (UI checkboxes): a list of job_ids restricts the route
    # to those jobs; absent => None (every job, back-compat). The set is posted on
    # every call (it lives in the client's session params), so it also gates advance.
    sel = payload.get("active_jobs")
    active_jobs = set(sel) if isinstance(sel, list) else None

    st = payload.get("state")
    if not st:                                   # session start
        levels = payload.get("job_levels", {})
        job_xp = {j: _XP_TABLE.xp_for_level(int(levels.get(j, 1))) for j in GATHERING_JOBS}
        # Seed the learned availability from the client's persisted map so even the
        # first window of a fresh session already avoids chronically-botted maps.
        pos, visited, avail = None, [], dict(payload.get("availability") or {})
    else:
        job_xp = {j: int(st.get("job_xp", {}).get(j, 0)) for j in GATHERING_JOBS}
        pos = st.get("pos")
        visited = list(st.get("visited", []))
        avail = dict(st.get("availability") or {})   # {"x,y": a}, persists across laps
        commit = payload.get("commit")
        if commit and commit.get("coord") is not None:
            coord = [int(commit["coord"][0]), int(commit["coord"][1])]
            key = _ckey(coord)
            kind = commit.get("kind")
            if kind is None:                     # back-compat: harvest bool -> advance/skip
                kind = "advance" if commit.get("harvest") else "skip"
            if kind == "advance":                # harvested full => observation 1
                job_xp, visited = f.advance(job_xp, visited, coord, active_jobs)
                pos = coord
                avail[key] = ewma_update(avail.get(key, 1.0), 1.0)
            elif kind == "empty":                # botted/empty => observation 0, don't advance
                visited.append(coord)            # not re-suggested this lap; a persists
                avail[key] = ewma_update(avail.get(key, 1.0), 0.0)
            else:                                # skip: "not now", no observation
                visited.append(coord)

    availability = _avail_to_coords(avail)
    levels_now = {j: _XP_TABLE.level_for_xp(job_xp[j]) for j in GATHERING_JOBS}
    auto = None
    if raw_engine == "auto":                     # pick engine + lambda from the levels
        auto = resolve_engine(levels_now, metric)  # recomputed each step (live levels)
        engine, lam = auto["engine"], float(auto["lambda_travel"])
    else:
        engine = "mcts" if raw_engine == "mcts" else "beam"

    if engine == "mcts":
        window = f.plan_window_mcts(pos, job_xp, visited, horizon=horizon,
                                    metric=metric, lambda_travel=lam,
                                    availability=availability, active_jobs=active_jobs)
    else:
        window = f.plan_window(pos, job_xp, visited, horizon=horizon,
                               metric=metric, lambda_travel=lam,
                               availability=availability, active_jobs=active_jobs)
    return {
        "window": window,
        "state": {"pos": pos, "job_xp": job_xp, "visited": visited, "availability": avail},
        "levels": levels_now,
        "metric": metric,
        "engine": engine,
        "lambda_travel": lam,
        "auto": auto,
        "done": not window,
        "job_labels": JOB_LABELS_FR,
    }


class Handler(BaseHTTPRequestHandler):
    server_version = "DofusJobs/0.1"

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802
        if self.path == "/" or self.path.startswith("/?"):
            self._send(200, render_index().encode("utf-8"), "text/html; charset=utf-8")
        elif self.path == "/healthz":
            self._send(200, b"ok", "text/plain")
        else:
            self._send(404, b"not found", "text/plain")

    def do_POST(self):  # noqa: N802
        handlers = {"/api/plan": plan}
        fn = handlers.get(self.path)
        if fn is None:
            self._send(404, b'{"error":"not found"}', "application/json")
            return
        try:
            length = int(self.headers.get("Content-Length", 0))
            payload = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
            result = fn(payload)
            self._send(200, json.dumps(result).encode("utf-8"), "application/json")
        except Exception as exc:  # noqa: BLE001
            body = json.dumps({"error": str(exc)}).encode("utf-8")
            self._send(400, body, "application/json")

    def log_message(self, *args):  # silence default logging
        pass


def main() -> None:
    ap = argparse.ArgumentParser(description="DofusJobs web app")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--open", action="store_true",
                    help="open the app in the default web browser on startup")
    args = ap.parse_args()
    get_data()
    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    url = f"http://{'127.0.0.1' if args.host in ('0.0.0.0', '') else args.host}:{args.port}"
    print(f"DofusJobs web app on {url}  (ferme cette fenetre pour arreter)")
    if args.open:
        import threading
        import webbrowser

        def _open():
            try:
                webbrowser.open(url)
            except Exception:       # noqa: BLE001  (never let browser issues break the app)
                pass
        threading.Timer(1.0, _open).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        srv.shutdown()


if __name__ == "__main__":
    main()
