"""Zero-dependency web app for DofusJobs (stdlib http.server only).

Routes:
  GET  /                 -> input form (XP/level per job, pods limit, lambda)
  POST /api/route        -> JSON {job_levels, pods_limit, lambda_travel, use_online}
  GET  /healthz          -> "ok"

Run:  python -m webapp.app  [--port 8000] [--online]
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
    JobXpTable,
    Optimizer,
    PlayerInput,
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


def compute(payload: dict) -> dict:
    tbl = _XP_TABLE
    # Accept either explicit job_xp or job_levels (mapped to start-of-level XP).
    if payload.get("job_xp"):
        job_xp = {j: int(payload["job_xp"].get(j, 0)) for j in GATHERING_JOBS}
    else:
        levels = payload.get("job_levels", {})
        job_xp = {j: tbl.xp_for_level(int(levels.get(j, 1))) for j in GATHERING_JOBS}
    pods_limit = int(payload.get("pods_limit", 0))
    lam = float(payload.get("lambda_travel", 1.0))
    metric = "xp" if payload.get("metric") == "xp" else "levels"
    start = payload.get("start_coords")
    start_coords = tuple(int(v) for v in start) if start else None

    resources, cells, maps = get_data()
    player = PlayerInput(job_xp=job_xp, pods_limit=pods_limit,
                         lambda_travel=lam, start_coords=start_coords, metric=metric)
    opt = Optimizer(resources, cells, maps=maps, xp_table=tbl)
    result = opt.optimize(player)
    out = result.to_dict()
    out["job_labels"] = JOB_LABELS_FR
    return out


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
        if self.path != "/api/route":
            self._send(404, b'{"error":"not found"}', "application/json")
            return
        try:
            length = int(self.headers.get("Content-Length", 0))
            payload = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
            result = compute(payload)
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
    args = ap.parse_args()
    get_data()
    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"DofusJobs web app on http://{args.host}:{args.port}")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        srv.shutdown()


if __name__ == "__main__":
    main()
