"""Service layer: loads data, runs forecasts, derives inventory / transfers / alerts, and applies edits.

The API layer only talks to this class. Results are cached per (data revision, scenario) and the cache is
dropped whenever inventory, scenario or decisions change.
"""
from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Callable

from . import alerts as alerts_mod
from . import data_gen, db, forecast as fc
from .config import Settings
from .domain import DEFAULT_AS_OF, DISEASE_IDS, MEDICINES, REGIONS, demo_today
from .history import History, load_history
from .inventory import (RISK_RANK, aggregate_cases, assess_pair, daily_demand, recommend_action,
                        weekly_medicine_demand)
from .redistribution import propose_transfers


class ApiError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status, self.message = status, message


@dataclass
class Snapshot:
    scenario: dict
    revision: int
    as_of: date
    today: date
    hist: History
    clinics: dict
    forecast: dict
    rows: dict            # (clinic, medicine) -> assessment
    demand: dict          # (clinic, medicine) -> {"central","high","weekly_central","weekly_high"}
    transfers: dict
    alerts: list
    outlook: dict


class Engine:
    def __init__(self, settings: Settings):
        self.s = settings
        self._lock = threading.RLock()
        self._cache: dict = {}
        self._hist_cache: tuple[str, History] | None = None
        self._forecaster: fc.Forecaster | None = None
        self._forecaster_stamp: float | None = None

    # ------------------------------------------------------------------ lifecycle
    def has_data(self) -> bool:
        if not self.s.db_path.is_file():
            return False
        with db.session(self.s.db_path) as conn:
            return bool(conn.execute("SELECT name FROM sqlite_master WHERE name='clinics'").fetchone()) and \
                db.one(conn, "SELECT COUNT(*) AS n FROM clinics")["n"] > 0

    def reset(self, as_of: date = DEFAULT_AS_OF) -> dict:
        with self._lock:
            summary = data_gen.reset_demo(self.s.db_path, as_of=as_of, seed=self.s.seed)
            self._cache.clear()
            self._hist_cache = None
            return summary

    def model_ready(self) -> bool:
        return fc.models_ready(self.s.model_dir)

    def train(self, progress: Callable[[str], None] = lambda _m: None) -> dict:
        with self._lock:
            with db.session(self.s.db_path) as conn:
                hist = load_history(conn)
            meta = fc.train(hist, self.s.model_dir, seed=self.s.seed, progress=progress)
            self._forecaster = None
            self._cache.clear()
            return meta

    def ensure_ready(self, progress: Callable[[str], None] = print) -> None:
        if not self.has_data():
            progress("No demo data found - generating the synthetic dataset ...")
            self.reset()
        if not self.model_ready():
            progress("No trained model found - training (about 20-40 seconds) ...")
            self.train(progress)

    def _forecaster_obj(self) -> fc.Forecaster:
        if not self.model_ready():
            raise ApiError(503, "Forecast model is not trained yet. Run: python -m pulsechain.cli train")
        stamp = (self.s.model_dir / "models.joblib").stat().st_mtime
        if self._forecaster is None or self._forecaster_stamp != stamp:
            self._forecaster, self._forecaster_stamp = fc.Forecaster(self.s.model_dir), stamp
        return self._forecaster

    # ------------------------------------------------------------------ state
    def revision(self, conn) -> int:
        return int(db.get_meta(conn, "revision", 0))

    def _bump(self, conn) -> int:
        rev = self.revision(conn) + 1
        db.set_meta(conn, "revision", rev)
        self._cache.clear()
        return rev

    def scenario(self, conn=None) -> dict:
        if conn is None:
            with db.session(self.s.db_path) as c:
                return self.scenario(c)
        sc = fc.default_scenario()
        sc.update(db.get_meta(conn, "scenario", {}) or {})
        return sc

    def set_scenario(self, data: dict) -> dict:
        """Validate and merge scenario settings (monsoon on/off, intensity, duration, regions)."""
        if not isinstance(data, dict):
            raise ApiError(400, "Request body must be a JSON object.")
        updates: dict = {}
        if "monsoon" in data:
            if not isinstance(data["monsoon"], bool):
                raise ApiError(400, "'monsoon' must be true or false.")
            updates["monsoon"] = data["monsoon"]
        if "intensity" in data:
            try:
                intensity = float(data["intensity"])
            except (TypeError, ValueError):
                raise ApiError(400, "'intensity' must be a number.")
            if isinstance(data["intensity"], bool) or not 1.0 <= intensity <= 6.0:
                raise ApiError(400, "'intensity' must be between 1 and 6 (multiplier on normal weekly rainfall).")
            updates["intensity"] = intensity
        if "weeks" in data:
            if not isinstance(data["weeks"], int) or isinstance(data["weeks"], bool) or not 1 <= data["weeks"] <= 6:
                raise ApiError(400, "'weeks' must be a whole number between 1 and 6.")
            updates["weeks"] = data["weeks"]
        if "regions" in data:
            regs = data["regions"]
            if not isinstance(regs, list) or not regs or any(r not in REGIONS for r in regs):
                raise ApiError(400, f"'regions' must be a non-empty list drawn from {sorted(REGIONS)}.")
            updates["regions"] = regs
        with self._lock, db.session(self.s.db_path) as conn:
            merged = {**self.scenario(conn), **updates}
            db.set_meta(conn, "scenario", merged)
            self._bump(conn)
            return merged

    # ------------------------------------------------------------------ snapshot
    def snapshot(self, scenario_override: dict | None = None) -> Snapshot:
        with self._lock, db.session(self.s.db_path) as conn:
            rev = self.revision(conn)
            scenario = scenario_override if scenario_override is not None else self.scenario(conn)
            key = (rev, json.dumps(scenario, sort_keys=True))
            if key in self._cache:
                return self._cache[key]
            snap = self._build(conn, scenario, rev)
            if len(self._cache) > 8:
                self._cache.pop(next(iter(self._cache)))
            self._cache[key] = snap
            return snap

    def baseline_snapshot(self) -> Snapshot:
        sc = fc.default_scenario()
        sc["monsoon"] = False
        return self.snapshot(sc)

    def _history(self, conn) -> History:
        stamp = db.get_meta(conn, "generated_at", "")
        if self._hist_cache is None or self._hist_cache[0] != stamp:
            self._hist_cache = (stamp, load_history(conn))
        return self._hist_cache[1]

    def _build(self, conn, scenario: dict, rev: int) -> Snapshot:
        hist = self._history(conn)
        forecaster = self._forecaster_obj()
        as_of = hist.as_of
        today = demo_today(as_of)
        clinics = {cid: dict(c) for cid, c in hist.clinics.items()}
        forecast = forecaster.forecast(hist, scenario)
        outlook = forecaster.outlook(hist, scenario)

        batches: dict[tuple[str, str], list[dict]] = {}
        for b in db.rows(conn, "SELECT * FROM inventory_batches WHERE quantity > 0 ORDER BY expiry_date, id"):
            batches.setdefault((b["clinic_id"], b["medicine_id"]), []).append(b)
        policy = {(p["clinic_id"], p["medicine_id"]): p["min_safety_stock"]
                  for p in db.rows(conn, "SELECT * FROM inventory_policy")}
        acked = {r["alert_key"] for r in db.rows(conn, "SELECT alert_key FROM alert_state")}

        rows, demand = {}, {}
        for cid, c in clinics.items():
            central = aggregate_cases(forecast[cid], "median")
            high = aggregate_cases(forecast[cid], "p90")
            for med in MEDICINES:
                wc = weekly_medicine_demand(med.id, c["catchment_pop"], central)
                wh = weekly_medicine_demand(med.id, c["catchment_pop"], high)
                dc, dh = daily_demand(wc), daily_demand(wh)
                row = assess_pair(clinic=c, medicine_id=med.id, batches=batches.get((cid, med.id), []),
                                  safety=policy[(cid, med.id)], today=today, demand_central=dc, demand_high=dh,
                                  recent_avg_weekly=float(hist.consumption[cid][med.id][-8:].mean()))
                row["action"] = recommend_action(row)
                rows[(cid, med.id)] = row
                demand[(cid, med.id)] = {"central": dc, "high": dh, "weekly_central": wc, "weekly_high": wh}

        transfers = propose_transfers(clinics, rows, demand, today)
        rejected = {r["proposal_key"] for r in db.rows(
            conn, "SELECT proposal_key FROM transfers WHERE status='rejected'")}
        transfers["proposals"] = [p for p in transfers["proposals"] if p["key"] not in rejected]
        alert_pack = alerts_mod.build_alerts(clinics, rows, forecast, hist, self.s.alert_risk_threshold, acked)
        return Snapshot(scenario=scenario, revision=rev, as_of=as_of, today=today, hist=hist, clinics=clinics,
                        forecast=forecast, rows=rows, demand=demand, transfers=transfers,
                        alerts=alert_pack["alerts"], outlook=outlook)

    # ------------------------------------------------------------------ inventory edits
    def set_batch_quantity(self, batch_id: int, quantity) -> dict:
        qty = _positive_int(quantity, "quantity", allow_zero=True, maximum=1_000_000)
        with self._lock, db.session(self.s.db_path) as conn:
            if not db.one(conn, "SELECT id FROM inventory_batches WHERE id = ?", (batch_id,)):
                raise ApiError(404, f"Batch {batch_id} not found.")
            conn.execute("UPDATE inventory_batches SET quantity = ? WHERE id = ?", (qty, batch_id))
            self._bump(conn)
            return db.one(conn, "SELECT * FROM inventory_batches WHERE id = ?", (batch_id,))

    def add_batch(self, clinic_id: str, medicine_id: str, quantity, expiry_date: str) -> dict:
        qty = _positive_int(quantity, "quantity", allow_zero=False, maximum=1_000_000)
        try:
            exp = date.fromisoformat(str(expiry_date))
        except ValueError:
            raise ApiError(400, "'expiry_date' must be an ISO date such as 2027-03-31.")
        with self._lock, db.session(self.s.db_path) as conn:
            if not db.one(conn, "SELECT id FROM clinics WHERE id = ?", (clinic_id,)):
                raise ApiError(404, f"Clinic {clinic_id} not found.")
            if medicine_id not in {m.id for m in MEDICINES}:
                raise ApiError(404, f"Medicine {medicine_id} not found.")
            as_of = date.fromisoformat(db.get_meta(conn, "as_of"))
            cur = conn.execute(
                "INSERT INTO inventory_batches(clinic_id, medicine_id, quantity, expiry_date, received_date, origin) VALUES (?,?,?,?,?,?)",
                (clinic_id, medicine_id, qty, exp.isoformat(), demo_today(as_of).isoformat(), "manual"))
            self._bump(conn)
            return db.one(conn, "SELECT * FROM inventory_batches WHERE id = ?", (cur.lastrowid,))

    # ------------------------------------------------------------------ transfers
    def decide_transfer(self, key: str, decision: str, note: str | None = None) -> dict:
        if decision not in {"approve", "reject"}:
            raise ApiError(400, "'decision' must be 'approve' or 'reject'.")
        snap = self.snapshot()
        proposal = next((p for p in snap.transfers["proposals"] if p["key"] == key), None)
        if proposal is None:
            raise ApiError(404, "That transfer proposal is no longer current (inventory may have changed). Refresh and review again.")
        with self._lock, db.session(self.s.db_path) as conn:
            if decision == "approve":
                for b in proposal["batches"]:
                    cur = conn.execute("UPDATE inventory_batches SET quantity = quantity - ? WHERE id = ? AND quantity >= ?",
                                       (b["quantity"], b["batch_id"], b["quantity"]))
                    if cur.rowcount != 1:
                        raise ApiError(409, "Donor stock changed since this proposal was made. Refresh and review again.")
                    conn.execute(
                        "INSERT INTO inventory_batches(clinic_id, medicine_id, quantity, expiry_date, received_date, origin) VALUES (?,?,?,?,?,?)",
                        (proposal["receiver_id"], proposal["medicine_id"], b["quantity"], b["expiry_date"],
                         proposal["arrival_date"], f"transfer:{proposal['donor_id']}"))
            conn.execute(
                "INSERT INTO transfers(proposal_key, medicine_id, donor_id, receiver_id, quantity, batches_json, status, decided_at, note) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (key, proposal["medicine_id"], proposal["donor_id"], proposal["receiver_id"], proposal["quantity"],
                 json.dumps(proposal["batches"]), "approved" if decision == "approve" else "rejected",
                 datetime.now(timezone.utc).isoformat(timespec="seconds"), note))
            self._bump(conn)
        return {"key": key, "status": "approved" if decision == "approve" else "rejected", "simulated": True}

    def transfer_history(self) -> list[dict]:
        with db.session(self.s.db_path) as conn:
            out = db.rows(conn, "SELECT * FROM transfers ORDER BY id DESC")
        for r in out:
            r["batches"] = json.loads(r.pop("batches_json"))
        return out

    # ------------------------------------------------------------------ alerts & SMS
    def acknowledge(self, key: str) -> dict:
        snap = self.snapshot()
        if not any(a["key"] == key for a in snap.alerts):
            raise ApiError(404, "Alert not found.")
        with self._lock, db.session(self.s.db_path) as conn:
            conn.execute("INSERT OR REPLACE INTO alert_state(alert_key, acknowledged_at) VALUES (?, ?)",
                         (key, datetime.now(timezone.utc).isoformat(timespec="seconds")))
            self._bump(conn)
        return {"key": key, "acknowledged": True}

    def sms_log(self, limit: int = 50) -> list[dict]:
        with db.session(self.s.db_path) as conn:
            return db.rows(conn, "SELECT * FROM sms_log ORDER BY id DESC LIMIT ?", (limit,))

    def meta(self) -> dict:
        with db.session(self.s.db_path) as conn:
            return {k: db.get_meta(conn, k) for k in ("as_of", "seed", "history_weeks", "generated_at", "data_label", "revision")}


def _positive_int(value, name: str, allow_zero: bool, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise ApiError(400, f"'{name}' must be a whole number.")
    try:
        f = float(value)
    except ValueError:
        raise ApiError(400, f"'{name}' must be a whole number.")
    if f != int(f):
        raise ApiError(400, f"'{name}' must be a whole number.")
    n = int(f)
    if n < (0 if allow_zero else 1) or n > maximum:
        raise ApiError(400, f"'{name}' must be between {0 if allow_zero else 1} and {maximum}.")
    return n
