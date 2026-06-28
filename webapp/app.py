"""Zero-dependency web app for DofusJobs (stdlib http.server only).

Routes:
  GET  /                 -> input form (level per job, lambda, metric)
  POST /api/plan         -> JSON {job_levels|state, horizon, lambda_travel, metric}
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
    load_dataset,
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
        f'<div class="jobrow"><label>{html.escape(JOB_LABELS_FR[j])}</label>'
        f'<input type="number" min="0" max="200" name="lvl_{j}" value="1"></div>'
        for j in GATHERING_JOBS
    )
    return tpl.replace("{{JOB_ROWS}}", rows)


_XP_TABLE = JobXpTable.load()


def _finder() -> FarmLoopFinder:
    resources, cells, maps = get_data()
    return FarmLoopFinder(resources, cells, maps=maps, xp_table=_XP_TABLE)


def plan(payload: dict) -> dict:
    """Interactive rolling planner: returns the next ``horizon`` maps from the
    current state, re-planned each call (beam lookahead). The client passes the
    opaque ``state`` back, plus an optional ``commit`` (the map it just did or
    skipped); the server applies it and re-plans."""
    f = _finder()
    horizon = max(1, min(40, int(payload.get("horizon", 20))))
    lam = float(payload.get("lambda_travel", 1.0))
    metric = "xp" if payload.get("metric") == "xp" else "levels"

    st = payload.get("state")
    if not st:                                   # session start
        levels = payload.get("job_levels", {})
        job_xp = {j: _XP_TABLE.xp_for_level(int(levels.get(j, 1))) for j in GATHERING_JOBS}
        pos, visited = None, []
    else:
        job_xp = {j: int(st.get("job_xp", {}).get(j, 0)) for j in GATHERING_JOBS}
        pos = st.get("pos")
        visited = list(st.get("visited", []))
        commit = payload.get("commit")
        if commit and commit.get("coord") is not None:
            coord = [int(commit["coord"][0]), int(commit["coord"][1])]
            if commit.get("harvest"):            # advance: harvest this map, move onto it
                job_xp, visited = f.advance(job_xp, visited, coord)
                pos = coord
            else:
                visited.append(coord)            # skip: just don't suggest it again

    window = f.plan_window(pos, job_xp, visited, horizon=horizon,
                           metric=metric, lambda_travel=lam)
    return {
        "window": window,
        "state": {"pos": pos, "job_xp": job_xp, "visited": visited},
        "levels": {j: _XP_TABLE.level_for_xp(job_xp[j]) for j in GATHERING_JOBS},
        "metric": metric,
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
