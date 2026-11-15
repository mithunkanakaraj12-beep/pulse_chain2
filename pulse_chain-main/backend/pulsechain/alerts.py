"""Builds inventory and disease-surge alerts and the short SMS text for each."""
from __future__ import annotations

from datetime import date, timedelta

from .domain import DISEASES, DISEASE_IDS, MEDICINE_BY_ID, medicines_for_disease
from .inventory import RISK_RANK, risk_at_least

SURGE_RATIO = 2.0
SURGE_MIN_INCREASE_PER_WEEK = 4.0
SMS_MAX_CHARS = 320   # two GSM-7 segments


def _short_date(iso: str | None) -> str:
    if not iso:
        return "now"
    return date.fromisoformat(iso).strftime("%d %b")


def _clip(text: str) -> str:
    return text if len(text) <= SMS_MAX_CHARS else text[: SMS_MAX_CHARS - 3].rstrip() + "..."


def stock_sms(row: dict) -> str:
    days = int(row["days_to_stockout"]) if row["days_to_stockout"] is not None else None
    trend = ""
    if row.get("demand_change_pct") is not None and row["demand_change_pct"] >= 10:
        trend = f"Forecast demand is rising (+{row['demand_change_pct']:.0f}%). "
    when = "now" if row["order_now"] else f"by {_short_date(row['order_by_date'])}"
    return _clip(
        f"ALERT: {row['clinic_name']} is projected to run low on {row['short_name']} in {days} days "
        f"(about {_short_date(row['stockout_date'])}). {trend}"
        f"Reorder {row['reorder_qty']} {row['unit']} {when}; delivery takes ~{row['lead_time_days']} days. "
        "Review stock and arrange replenishment.")


def surge_sms(clinic: dict, disease_name: str, recent: float, forecast: float, pct: float, meds: list[str]) -> str:
    return _clip(
        f"WARNING: {clinic['name']}: {disease_name} cases forecast to rise ~{pct:.0f}% over the next 4 weeks "
        f"({recent:.0f} to {forecast:.0f} per week). Check {', '.join(meds)} stock and brief staff.")


def build_alerts(clinics: dict[str, dict], rows: dict, forecast: dict, hist, threshold: str, acknowledged: set[str]) -> dict:
    alerts: list[dict] = []
    for (cid, med_id), r in rows.items():
        if not risk_at_least(r["risk"], "moderate"):
            continue
        key = f"stock:{cid}:{med_id}"
        eligible = risk_at_least(r["risk"], threshold)
        alerts.append({
            "key": key, "type": "stock", "severity": r["risk"], "clinic_id": cid, "clinic_name": r["clinic_name"],
            "medicine_id": med_id, "disease_id": None,
            "title": f"{r['short_name'].capitalize()} shortage risk at {r['clinic_name']}",
            "message": r["action"], "days_to_stockout": r["days_to_stockout"], "stockout_date": r["stockout_date"],
            "reorder_qty": r["reorder_qty"], "unit": r["unit"], "order_by_date": r["order_by_date"],
            "sms_eligible": eligible, "sms_body": stock_sms(r) if eligible else None,
            "to_number": clinics[cid]["phone"], "health_worker": clinics[cid]["health_worker"],
            "acknowledged": key in acknowledged,
        })
    for cid, c in clinics.items():
        for d in DISEASES:
            recent = float(hist.cases[cid][d.id][-4:].mean())
            fut = float(sum(forecast[cid][d.id]["median"][:4]) / 4.0)
            if fut >= SURGE_RATIO * max(recent, 1.0) and fut - recent >= SURGE_MIN_INCREASE_PER_WEEK:
                pct = (fut / max(recent, 1.0) - 1.0) * 100.0
                meds = [MEDICINE_BY_ID[m].short_name for m in medicines_for_disease(d.id)]
                key = f"surge:{cid}:{d.id}"
                sev = "high" if pct >= 150 else "moderate"
                alerts.append({
                    "key": key, "type": "surge", "severity": sev, "clinic_id": cid, "clinic_name": c["name"],
                    "medicine_id": None, "disease_id": d.id,
                    "title": f"{d.name} surge forecast at {c['name']}",
                    "message": f"{d.name} cases forecast to rise from about {recent:.0f} to {fut:.0f} per week over the next 4 weeks.",
                    "days_to_stockout": None, "stockout_date": None, "reorder_qty": None, "unit": None, "order_by_date": None,
                    "sms_eligible": risk_at_least(sev, threshold),
                    "sms_body": surge_sms(c, d.name, recent, fut, pct, meds) if risk_at_least(sev, threshold) else None,
                    "to_number": c["phone"], "health_worker": c["health_worker"], "acknowledged": key in acknowledged,
                })
    alerts.sort(key=lambda a: (-RISK_RANK[a["severity"]], a["days_to_stockout"] if a["days_to_stockout"] is not None else 1e9, a["key"]))
    return {"alerts": alerts}


def replenishment(rows: dict, today: date, within_days: int = 14) -> list[dict]:
    """Rows that need an order placed soon (or already overdue)."""
    cutoff = today + timedelta(days=within_days)
    items = []
    for r in rows.values():
        if r["reorder_qty"] > 0 and r["order_by_date"] and date.fromisoformat(r["order_by_date"]) <= cutoff:
            items.append({k: r[k] for k in ("clinic_id", "clinic_name", "medicine_id", "short_name", "unit", "reorder_qty",
                                            "order_by_date", "order_now", "risk", "days_to_stockout", "lead_time_days")})
    return sorted(items, key=lambda i: (i["order_by_date"], -RISK_RANK[i["risk"]]))
