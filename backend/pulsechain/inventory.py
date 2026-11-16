"""Inventory forecasting: turns forecast disease cases into medicine demand and stock-out dates.

Every number is computed by the transparent, documented rules below (no model output is used as an
inventory figure directly):

1. Weekly demand  = routine baseline use (per 1,000 population) + sum(forecast cases x units per case).
2. Daily demand   = weekly demand / 7; weeks beyond the 8-week forecast repeat the last forecast week.
3. Stock depletion is simulated day by day, first-expiry-first-out. Expired batches are excluded and
   batches that expire before they can be used are counted as waste, not as available stock.
4. days_to_stockout  : day the usable stock reaches zero.   days_to_safety : day it falls to the clinic's
   minimum safety stock.
5. margin = days_to_stockout - supplier lead time. Risk depends on that margin:
      critical : margin < 0   (stock runs out before an order placed today could arrive)
      high     : margin < 10 days
      moderate : margin < 21 days
      low      : otherwise
6. Reorder quantity = demand over (lead time + 28 days) + safety stock - usable stock, rounded up to the
   medicine's order multiple. Order-by date = day the stock reaches safety level minus the lead time.

A pessimistic "high demand" case uses the 90th-percentile case forecast.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, timedelta

import numpy as np

from .domain import DISEASE_IDS, MEDICINE_BY_ID, TREATMENT_PROTOCOL

SIM_HORIZON_DAYS = 180
TARGET_COVER_DAYS = 28
RISK_ORDER = ["low", "moderate", "high", "critical"]
RISK_RANK = {r: i for i, r in enumerate(RISK_ORDER)}
HIGH_MARGIN_DAYS = 10
MODERATE_MARGIN_DAYS = 21


def risk_from_margin(margin_days: float | None) -> str:
    if margin_days is None:
        return "low"
    if margin_days < 0:
        return "critical"
    if margin_days < HIGH_MARGIN_DAYS:
        return "high"
    if margin_days < MODERATE_MARGIN_DAYS:
        return "moderate"
    return "low"


def risk_at_least(risk: str, threshold: str) -> bool:
    return RISK_RANK[risk] >= RISK_RANK[threshold]


def weekly_medicine_demand(medicine_id: str, pop: int, cases_by_disease: dict[str, list[float]]) -> list[float]:
    """Weekly units of `medicine_id` needed given weekly case forecasts for each disease."""
    med = MEDICINE_BY_ID[medicine_id]
    base = med.baseline_weekly_per_1000 * pop / 1000.0
    n = len(next(iter(cases_by_disease.values())))
    demand = [base] * n
    for disease, rate in TREATMENT_PROTOCOL.get(medicine_id, {}).items():
        for i, c in enumerate(cases_by_disease[disease]):
            demand[i] += c * rate
    return demand


def daily_demand(weekly: list[float], days: int = SIM_HORIZON_DAYS) -> np.ndarray:
    out = np.empty(days)
    for d in range(days):
        out[d] = weekly[min(d // 7, len(weekly) - 1)] / 7.0
    return out


@dataclass
class SimResult:
    days_to_stockout: float | None
    days_to_safety: float | None
    wasted_units: float


def simulate_stock(batches: list[tuple[date, float]], demand: np.ndarray, today: date, safety: float) -> SimResult:
    """Day-by-day FEFO depletion. `batches` = [(expiry_date, quantity)]; already-expired batches are ignored."""
    live = sorted([[exp, float(q)] for exp, q in batches if exp >= today and q > 0], key=lambda b: b[0])
    total = sum(q for _e, q in live)
    stockout = safety_day = None
    wasted = 0.0
    if total <= 0:
        return SimResult(0.0, 0.0, 0.0)
    if total <= safety:
        safety_day = 0.0
    for d in range(len(demand)):
        day = today + timedelta(days=d)
        expired = [b for b in live if b[0] < day]
        if expired:
            wasted += sum(b[1] for b in expired)
            live = [b for b in live if b[0] >= day]
        before = sum(b[1] for b in live)
        need = float(demand[d])
        if before <= 1e-9:
            stockout = float(d) if stockout is None else stockout
            break
        if safety_day is None and before - need <= safety and need > 0:
            safety_day = d + min(1.0, max(0.0, (before - safety) / need))
        if need >= before - 1e-9:
            stockout = d + (before / need if need > 0 else 0.0)
            break
        left = need
        for b in live:
            take = min(b[1], left)
            b[1] -= take
            left -= take
            if left <= 1e-9:
                break
        live = [b for b in live if b[1] > 1e-9]
    return SimResult(stockout, safety_day, wasted)


def _round_up(qty: float, multiple: int) -> int:
    if qty <= 0:
        return 0
    return int(math.ceil(qty / multiple) * multiple)


def _fmt_days(d: float | None) -> int | None:
    return None if d is None else int(math.floor(d))


def assess_pair(*, clinic: dict, medicine_id: str, batches: list[dict], safety: int, today: date,
                demand_central: np.ndarray, demand_high: np.ndarray, recent_avg_weekly: float,
                recent_cases_note: str = "") -> dict:
    """Full inventory assessment for one clinic x medicine."""
    med = MEDICINE_BY_ID[medicine_id]
    lead = int(clinic["lead_time_days"])
    pairs = [(date.fromisoformat(b["expiry_date"]), b["quantity"]) for b in batches]
    usable = sum(q for e, q in pairs if e >= today)
    expired_units = sum(q for e, q in pairs if e < today)
    central = simulate_stock(pairs, demand_central, today, safety)
    high = simulate_stock(pairs, demand_high, today, safety)

    margin = None if central.days_to_stockout is None else central.days_to_stockout - lead
    risk = risk_from_margin(margin)
    stock_days = central.days_to_stockout
    stockout_date = None if stock_days is None else today + timedelta(days=int(math.floor(stock_days)))

    horizon = lead + TARGET_COVER_DAYS
    need = float(demand_central[:horizon].sum()) + safety - usable
    reorder_qty = _round_up(need, med.order_multiple)
    order_by = None
    order_now = False
    if central.days_to_safety is not None:
        slack = central.days_to_safety - lead
        order_by = today + timedelta(days=int(math.floor(max(0.0, slack))))
        order_now = slack <= 0
    elif reorder_qty > 0:
        order_by, order_now = today, True

    avg_daily_now = recent_avg_weekly / 7.0
    forecast_daily = float(demand_central[:28].mean())
    usable_batches = sorted([(e, q) for e, q in pairs if e >= today])
    return {
        "clinic_id": clinic["id"], "clinic_name": clinic["name"], "region": clinic["region"],
        "medicine_id": medicine_id, "medicine_name": med.name, "short_name": med.short_name, "unit": med.unit,
        "usable_stock": int(usable), "expired_units": int(expired_units), "safety_stock": int(safety),
        "lead_time_days": lead,
        "avg_daily_usage": round(avg_daily_now, 2),
        "forecast_daily_usage_4w": round(forecast_daily, 2),
        "demand_change_pct": round((forecast_daily / avg_daily_now - 1) * 100, 1) if avg_daily_now > 0 else None,
        "days_to_stockout": None if stock_days is None else round(stock_days, 1),
        "days_to_stockout_high_demand": None if high.days_to_stockout is None else round(high.days_to_stockout, 1),
        "stockout_date": stockout_date.isoformat() if stockout_date else None,
        "days_to_safety_stock": None if central.days_to_safety is None else round(central.days_to_safety, 1),
        "margin_days": None if margin is None else round(margin, 1),
        "risk": risk,
        "reorder_qty": reorder_qty,
        "order_by_date": order_by.isoformat() if order_by else None,
        "order_now": order_now,
        "projected_waste_units": int(round(central.wasted_units)),
        "nearest_expiry": usable_batches[0][0].isoformat() if usable_batches else None,
        "batches": [{"id": b["id"], "quantity": b["quantity"], "expiry_date": b["expiry_date"],
                     "received_date": b.get("received_date"), "origin": b.get("origin"),
                     "expired": date.fromisoformat(b["expiry_date"]) < today} for b in batches],
    }


def recommend_action(row: dict) -> str:
    """One-line, plain-language recommendation for a clinic x medicine row."""
    name, unit = row["short_name"], row["unit"]
    if row["risk"] == "critical":
        return (f"Emergency: {name} will run out in about {_fmt_days(row['days_to_stockout'])} days, before a normal "
                f"order ({row['lead_time_days']} days) can arrive. Request a transfer from a nearby clinic and order "
                f"{row['reorder_qty']} {unit} now.")
    if row["risk"] == "high":
        return (f"Order {row['reorder_qty']} {unit} of {name} now; stock lasts about "
                f"{_fmt_days(row['days_to_stockout'])} days against a {row['lead_time_days']}-day delivery time.")
    if row["risk"] == "moderate":
        return (f"Plan a reorder of {row['reorder_qty']} {unit} of {name} by {row['order_by_date']}.")
    return f"Stock of {name} is adequate for the forecast period."


def aggregate_cases(forecast_for_clinic: dict, key: str) -> dict[str, list[float]]:
    return {d: list(forecast_for_clinic[d][key]) for d in DISEASE_IDS}
