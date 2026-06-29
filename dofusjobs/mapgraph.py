"""Real world-map travel graph with breadth-first shortest paths.

Nodes are the real main-world map coordinates (DofusDB worldMap=1). Two maps are
connected when they are cardinally adjacent and both exist, so paths follow the
actual continent layout (no walking across missing/sea maps). Every edge costs 1
map-screen transition.

Distances are computed with single-source BFS: one BFS from the current position
yields the distance to *every* cell at once, which is what the optimizer needs
each step (far cheaper than per-pair A* over thousands of nodes). Results are
cached per source.
"""

from __future__ import annotations

from collections import deque
from typing import Dict, Iterable, Optional, Set, Tuple

Coord = Tuple[int, int]

# Dofus world convention: +x = Est (→), -x = Ouest (←), +y = Sud (↓), -y = Nord (↑).
_DIR_ARROW: Dict[Tuple[int, int], str] = {
    (1, 0): "→", (-1, 0): "←", (0, 1): "↓", (0, -1): "↑"}


def path_directions(path) -> list:
    """Turn a coord path into compact arrow step directions, e.g.
    ``[(-1,-29),(0,-29),(1,-29),(1,-28)]`` -> ``["→×2", "↓"]``.

    Consecutive identical steps are merged, but the order is preserved (never
    reordered) so following them only ever crosses maps that actually exist.
    A non-adjacent hop (different graph components, i.e. zaap/boat) is labelled.
    """
    if not path or len(path) < 2:
        return []
    steps = []
    for (x0, y0), (x1, y1) in zip(path, path[1:]):
        steps.append(_DIR_ARROW.get((x1 - x0, y1 - y0), "saut (zaap/bateau)"))
    out = []
    run, n = steps[0], 1
    for d in steps[1:]:
        if d == run:
            n += 1
        else:
            out.append(f"{run}×{n}" if n > 1 else run)
            run, n = d, 1
    out.append(f"{run}×{n}" if n > 1 else run)
    return out


class MapGraph:
    def __init__(self, maps: Iterable[Coord]) -> None:
        self.nodes: Set[Coord] = {(int(x), int(y)) for x, y in maps}
        self._bfs_cache: Dict[Coord, Dict[Coord, int]] = {}
        self._components = None

    def _neighbours(self, c: Coord):
        x, y = c
        for nb in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
            if nb in self.nodes:
                yield nb

    def distances_from(self, src: Coord, max_dist: Optional[int] = None) -> Dict[Coord, int]:
        """BFS distance (in map-screen transitions) from ``src`` to every
        reachable map within ``max_dist`` (unbounded if None). Cached per source;
        a cached unbounded result is reused to satisfy bounded requests."""
        src = (int(src[0]), int(src[1]))
        key = (src, max_dist)
        cached = self._bfs_cache.get(key) or self._bfs_cache.get((src, None))
        if cached is not None:
            return cached
        dist: Dict[Coord, int] = {src: 0}
        if src in self.nodes:
            dq = deque([src])
            while dq:
                cur = dq.popleft()
                d = dist[cur] + 1
                if max_dist is not None and d > max_dist:
                    continue
                for nb in self._neighbours(cur):
                    if nb not in dist:
                        dist[nb] = d
                        dq.append(nb)
        self._bfs_cache[key] = dist
        return dist

    def components(self):
        """Connected components of the map graph, as a list of coord-sets
        (largest first). The world map has many disconnected islands reachable
        only by boat/zaap, which a single walking pass cannot cross. Cached."""
        if self._components is None:
            seen: Set[Coord] = set()
            comps = []
            for n in self.nodes:
                if n in seen:
                    continue
                comp: Set[Coord] = set()
                dq = deque([n])
                seen.add(n)
                while dq:
                    cur = dq.popleft()
                    comp.add(cur)
                    for nb in self._neighbours(cur):
                        if nb not in seen:
                            seen.add(nb)
                            dq.append(nb)
                comps.append(comp)
            comps.sort(key=len, reverse=True)
            self._components = comps
        return self._components

    def shortest_path(self, a: Coord, b: Coord):
        """The list of maps from ``a`` to ``b`` inclusive (a real BFS path along
        existing adjacent maps), or ``None`` if ``b`` is unreachable from ``a``
        (different connected components)."""
        a = (int(a[0]), int(a[1])); b = (int(b[0]), int(b[1]))
        if a == b:
            return [a]
        if a not in self.nodes or b not in self.nodes:
            return None
        prev: Dict[Coord, Coord] = {a: a}
        dq = deque([a])
        while dq:
            cur = dq.popleft()
            if cur == b:
                break
            for nb in self._neighbours(cur):
                if nb not in prev:
                    prev[nb] = cur
                    dq.append(nb)
        if b not in prev:
            return None
        path = [b]
        while path[-1] != a:
            path.append(prev[path[-1]])
        path.reverse()
        return path
