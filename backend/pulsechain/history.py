"""Loads the weekly history from SQLite into aligned numpy arrays (one set per clinic)."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

import numpy as np

from . import db
from .domain import DISEASE_IDS, MEDICINES


@dataclass
class History:
    weeks: list[date]
    clinics: dict[str, dict]                      # clinic_id -> row dict (insertion order = id order)
    rain: dict[str, np.ndarray] = field(default_factory=dict)
    temp: dict[str, np.ndarray] = field(default_factory=dict)
    cases: dict[str, dict[str, np.ndarray]] = field(default_factory=dict)
    consumption: dict[str, dict[str, np.ndarray]] = field(default_factory=dict)

    @property
    def n(self) -> int:
        return len(self.weeks)

    @property
    def as_of(self) -> date:
        return self.weeks[-1]


def load_history(conn) -> History:
    clinics = {r["id"]: r for r in db.rows(conn, "SELECT * FROM clinics ORDER BY id")}
    if not clinics:
        raise RuntimeError("Database is empty. Run: python -m pulsechain.cli reset")
    weeks_txt = [r["week_start"] for r in db.rows(conn, "SELECT DISTINCT week_start FROM weather ORDER BY week_start")]
    index = {w: i for i, w in enumerate(weeks_txt)}
    n = len(weeks_txt)
    hist = History(weeks=[date.fromisoformat(w) for w in weeks_txt], clinics=clinics)
    for cid in clinics:
        hist.rain[cid] = np.zeros(n)
        hist.temp[cid] = np.zeros(n)
        hist.cases[cid] = {d: np.zeros(n) for d in DISEASE_IDS}
        hist.consumption[cid] = {m.id: np.zeros(n) for m in MEDICINES}
    for r in conn.execute("SELECT clinic_id, week_start, rain_mm, temp_c FROM weather"):
        i = index[r["week_start"]]
        hist.rain[r["clinic_id"]][i] = r["rain_mm"]
        hist.temp[r["clinic_id"]][i] = r["temp_c"]
    for r in conn.execute("SELECT clinic_id, disease, week_start, cases FROM cases"):
        hist.cases[r["clinic_id"]][r["disease"]][index[r["week_start"]]] = r["cases"]
    for r in conn.execute("SELECT clinic_id, medicine_id, week_start, units FROM consumption"):
        hist.consumption[r["clinic_id"]][r["medicine_id"]][index[r["week_start"]]] = r["units"]
    return hist
