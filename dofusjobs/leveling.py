"""Gathering-job XP <-> level conversion (drives the in-plan level-up simulation
and the 'percent of level' objective).

Cumulative job-XP thresholds are loaded from data/job_xp_table.json (Wiki Dofus
decade anchors, linearly interpolated between anchors). All gathering jobs share
the same curve. The level cap is the table's top anchor.
"""

from __future__ import annotations

import json
import os
from typing import Dict, List

_DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")


class JobXpTable:
    def __init__(self, thresholds: List[int]) -> None:
        # thresholds[L] = cumulative XP required to be level L (index 0 unused).
        self.thresholds = thresholds
        self.max_level = len(thresholds) - 1

    @classmethod
    def load(cls, path: str | None = None) -> "JobXpTable":
        path = path or os.path.join(_DATA_DIR, "job_xp_table.json")
        with open(path, "r", encoding="utf-8") as fh:
            spec = json.load(fh)
        if "formula" in spec:
            return cls(cls._from_formula(spec["formula"]))
        anchors = {int(k): int(v) for k, v in spec["anchors"].items()}
        return cls(cls._interpolate(anchors))

    @staticmethod
    def _from_formula(formula: Dict) -> List[int]:
        """Closed-form cumulative XP. Currently the 'triangular' model:
        cumulative(L) = coefficient * L * (L - 1) (each level costs 20*coefficient
        more than the previous, with coefficient*2 the first-level cost)."""
        coef = int(formula.get("coefficient", 10))
        max_level = int(formula.get("max_level", 200))
        return [coef * L * (L - 1) for L in range(max_level + 1)]

    @staticmethod
    def _interpolate(anchors: Dict[int, int]) -> List[int]:
        levels = sorted(anchors)
        max_level = levels[-1]
        thr = [0] * (max_level + 1)
        for i in range(len(levels) - 1):
            lo, hi = levels[i], levels[i + 1]
            xlo, xhi = anchors[lo], anchors[hi]
            for L in range(lo, hi + 1):
                frac = (L - lo) / (hi - lo)
                thr[L] = round(xlo + frac * (xhi - xlo))
        return thr

    def level_for_xp(self, xp: int) -> int:
        """Highest level whose cumulative threshold is <= xp (clamped 1..max)."""
        xp = max(0, int(xp))
        lvl = 1
        for L in range(1, self.max_level + 1):
            if self.thresholds[L] <= xp:
                lvl = L
            else:
                break
        return lvl

    def xp_for_level(self, level: int) -> int:
        """Cumulative XP at the start of ``level``."""
        return self.thresholds[max(1, min(self.max_level, int(level)))]

    def level_progress(self, xp: float) -> float:
        """Continuous level: integer level + fraction into the current level band.

        E.g. exactly at the level-L threshold -> L.0; halfway to L+1 -> L+0.5.
        Capped at max_level. This is the unit behind the 'percent of a level'
        objective: a harvest's worth = level_progress(after) - level_progress(before)."""
        xp = max(0.0, float(xp))
        L = self.level_for_xp(int(xp))
        if L >= self.max_level:
            return float(self.max_level)
        lo, hi = self.thresholds[L], self.thresholds[L + 1]
        if hi <= lo:
            return float(L)
        return L + (xp - lo) / (hi - lo)
