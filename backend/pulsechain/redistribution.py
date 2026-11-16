"""Village-to-village redistribution: recommends transfers from surplus clinics to clinics facing shortages.

All outputs are RECOMMENDATIONS that need human approval. The rules (applied in this order):

Receivers : clinics whose risk for a medicine is moderate or worse and that need a reorder.
Donors    : clinics whose own risk for that medicine is low. A donor may only give away stock beyond what
            it needs: usable stock - (pessimistic 90th-percentile demand over its own lead time + 28 days
            + minimum safety stock). The donor's safety stock is therefore never touched.
Batches   : expired batches are never moved; a batch needs >= 90 days of shelf life left on arrival, and the
            receiver must be able to use the quantity before it expires (14-day buffer). Earliest-expiry
            eligible batches go first. Only the same medicine / formulation is ever matched.
Transport : estimated road distance (1.3 x straight line) <= 140 km and <= 6 h at the clinics' average road
            speed, and the delivery (1 day to dispatch + travel) must arrive before the receiver runs out.
Matching  : most urgent receiver first; nearest feasible donor first; a donor's surplus is shared out once.
"""
from __future__ import annotations

import math
from datetime import date, timedelta

import numpy as np

from .geo import haversine_km, road_km
from .inventory import RISK_RANK, simulate_stock
from .domain import MEDICINE_BY_ID

MIN_SHELF_DAYS_AT_ARRIVAL = 90
USE_BEFORE_EXPIRY_BUFFER_DAYS = 14
MAX_ROAD_KM = 140.0
MAX_TRAVEL_HOURS = 6.0
DISPATCH_DAYS = 1.0
MIN_TRANSFER_UNITS = 10
DONOR_COVER_DAYS = 28


def travel_info(a: dict, b: dict) -> dict:
    km = road_km(a["lat"], a["lon"], b["lat"], b["lon"])
    speed = (a["road_speed_kmh"] + b["road_speed_kmh"]) / 2.0
    return {"road_km": round(km, 1), "straight_km": round(haversine_km(a["lat"], a["lon"], b["lat"], b["lon"]), 1),
            "travel_hours": round(km / speed, 1)}


def nearby_clinics(clinics: dict[str, dict], clinic_id: str, limit: int = 4) -> list[dict]:
    me = clinics[clinic_id]
    res = []
    for cid, c in clinics.items():
        if cid != clinic_id:
            res.append({"clinic_id": cid, "name": c["name"], "region": c["region"], **travel_info(me, c)})
    return sorted(res, key=lambda r: r["road_km"])[:limit]


def donor_transferable(row: dict, demand_high: np.ndarray) -> int:
    """Units a clinic could give away while keeping its own pessimistic needs and safety stock."""
    horizon = int(row["lead_time_days"]) + DONOR_COVER_DAYS
    reserve = float(demand_high[:horizon].sum()) + row["safety_stock"]
    return max(0, int(math.floor(row["usable_stock"] - reserve)))


def propose_transfers(clinics: dict[str, dict], rows: dict[tuple[str, str], dict],
                      demand: dict[tuple[str, str], dict], today: date) -> dict:
    """rows[(clinic, medicine)] = inventory assessment; demand[(clinic, medicine)] = {'central','high'} daily arrays."""
    proposals: list[dict] = []
    surplus_list: list[dict] = []
    shortage_list: list[dict] = []
    medicine_ids = sorted({m for _c, m in rows})

    for med_id in medicine_ids:
        med = MEDICINE_BY_ID[med_id]
        donors: dict[str, dict] = {}
        for cid in clinics:
            r = rows[(cid, med_id)]
            if r["risk"] == "low":
                t = donor_transferable(r, demand[(cid, med_id)]["high"])
                if t >= MIN_TRANSFER_UNITS:
                    donors[cid] = {"remaining": t, "batch_left": {b["id"]: b["quantity"] for b in r["batches"] if not b["expired"]}}
                    surplus_list.append({"clinic_id": cid, "clinic_name": r["clinic_name"], "medicine_id": med_id,
                                         "short_name": r["short_name"], "unit": r["unit"], "transferable_units": t,
                                         "usable_stock": r["usable_stock"]})
        receivers = sorted((r for (cid, m), r in rows.items() if m == med_id and RISK_RANK[r["risk"]] >= RISK_RANK["moderate"]
                            and r["reorder_qty"] > 0), key=lambda r: (r["days_to_stockout"] if r["days_to_stockout"] is not None else 1e9))
        for r in receivers:
            shortage_list.append({"clinic_id": r["clinic_id"], "clinic_name": r["clinic_name"], "medicine_id": med_id,
                                  "short_name": r["short_name"], "unit": r["unit"], "risk": r["risk"],
                                  "days_to_stockout": r["days_to_stockout"], "units_needed": r["reorder_qty"]})
        for r in receivers:
            rid = r["clinic_id"]
            need = r["reorder_qty"]
            received_total = 0
            daily_recv = float(demand[(rid, med_id)]["central"][:28].mean())
            for did in sorted(donors, key=lambda d: travel_info(clinics[d], clinics[rid])["road_km"]):
                if need < MIN_TRANSFER_UNITS:
                    break
                info = travel_info(clinics[did], clinics[rid])
                if info["road_km"] > MAX_ROAD_KM or info["travel_hours"] > MAX_TRAVEL_HOURS:
                    continue
                arrival_days = DISPATCH_DAYS + info["travel_hours"] / 24.0
                if r["days_to_stockout"] is not None and r["days_to_stockout"] <= arrival_days:
                    continue
                arrival = today + timedelta(days=math.ceil(arrival_days))
                d = donors[did]
                moved, picked = 0, []
                donor_row = rows[(did, med_id)]
                for b in sorted((b for b in donor_row["batches"] if not b["expired"]), key=lambda b: b["expiry_date"]):
                    shelf = (date.fromisoformat(b["expiry_date"]) - arrival).days
                    if shelf < MIN_SHELF_DAYS_AT_ARRIVAL:
                        continue
                    usable_cap = int(math.floor(daily_recv * (shelf - USE_BEFORE_EXPIRY_BUFFER_DAYS))) - received_total - moved
                    qty = min(need - moved, d["remaining"] - moved, d["batch_left"].get(b["id"], 0), usable_cap)
                    if qty <= 0:
                        continue
                    picked.append({"batch_id": b["id"], "quantity": int(qty), "expiry_date": b["expiry_date"], "shelf_days_at_arrival": shelf})
                    moved += qty
                    if moved >= need or moved >= d["remaining"]:
                        break
                if moved < MIN_TRANSFER_UNITS:
                    continue
                for p in picked:
                    d["batch_left"][p["batch_id"]] -= p["quantity"]
                d["remaining"] -= moved
                need -= moved
                received_total += moved

                # Effect of the transfer on both clinics (same FEFO simulation as the inventory engine).
                new_recv = [(date.fromisoformat(b["expiry_date"]), b["quantity"]) for b in r["batches"]] + \
                           [(date.fromisoformat(p["expiry_date"]), p["quantity"]) for p in picked]
                recv_after = simulate_stock(new_recv, demand[(rid, med_id)]["central"], today, r["safety_stock"])
                taken = {p["batch_id"]: p["quantity"] for p in picked}
                donor_after = simulate_stock(
                    [(date.fromisoformat(b["expiry_date"]), b["quantity"] - taken.get(b["id"], 0)) for b in donor_row["batches"]],
                    demand[(did, med_id)]["high"], today, donor_row["safety_stock"])
                proposals.append({
                    "key": f"{med_id}:{did}:{rid}", "medicine_id": med_id, "medicine_name": med.name,
                    "short_name": med.short_name, "unit": med.unit,
                    "donor_id": did, "donor_name": clinics[did]["name"],
                    "receiver_id": rid, "receiver_name": clinics[rid]["name"],
                    "quantity": int(moved), "batches": picked,
                    "road_km": info["road_km"], "straight_km": info["straight_km"], "travel_hours": info["travel_hours"],
                    "arrival_date": arrival.isoformat(),
                    "receiver_risk_before": r["risk"], "receiver_days_before": r["days_to_stockout"],
                    "receiver_days_after": None if recv_after.days_to_stockout is None else round(recv_after.days_to_stockout, 1),
                    "donor_days_after_high_demand": None if donor_after.days_to_stockout is None else round(donor_after.days_to_stockout, 1),
                    "donor_safety_stock": donor_row["safety_stock"],
                    "status": "pending", "requires_approval": True,
                    "rationale": (f"{clinics[did]['name']} holds more {med.short_name} than it needs "
                                  f"(its own 90th-percentile demand plus safety stock stay covered). "
                                  f"{clinics[rid]['name']} is projected to run out in "
                                  f"{math.floor(r['days_to_stockout'])} days; the transfer arrives in about "
                                  f"{arrival_days:.1f} days over ~{info['road_km']:.0f} km."),
                })
    proposals.sort(key=lambda p: (RISK_RANK[p["receiver_risk_before"]] * -1, p["receiver_days_before"] or 1e9, p["road_km"]))
    return {"proposals": proposals, "surplus": surplus_list, "shortages": shortage_list}
