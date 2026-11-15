"""Pure functions that shape engine snapshots into the JSON the frontend consumes."""
from __future__ import annotations

from datetime import date, timedelta

from . import alerts as alerts_mod
from .domain import (DATA_LABEL, DISEASES, DISEASE_IDS, MEDICINES, MEDICINE_BY_ID, REGIONS, TREATMENT_PROTOCOL,
                     medicines_for_disease)
from .engine import Snapshot
from .inventory import RISK_ORDER, RISK_RANK
from .redistribution import nearby_clinics

HIST_WEEKS = 26
DISEASE_NAME = {d.id: d.name for d in DISEASES}


def _ids(snap: Snapshot, region: str | None) -> list[str]:
    return [cid for cid, c in snap.clinics.items() if not region or region == "all" or c["region"] == region]


def disease_series(snap: Snapshot, disease: str, clinic_ids: list[str], weeks_hist: int = HIST_WEEKS, weeks_ahead: int = 8) -> dict:
    h = snap.hist
    hist_weeks = h.weeks[-weeks_hist:]
    history = [{"week": w.isoformat(), "value": float(sum(h.cases[c][disease][-weeks_hist + i] for c in clinic_ids))}
               for i, w in enumerate(hist_weeks)]
    first = snap.forecast[clinic_ids[0]][disease]["week_start"]
    fcast = []
    for i in range(weeks_ahead):
        fcast.append({"week": first[i],
                      "median": round(sum(snap.forecast[c][disease]["median"][i] for c in clinic_ids), 1),
                      "lo": round(sum(snap.forecast[c][disease]["p10"][i] for c in clinic_ids), 1),
                      "hi": round(sum(snap.forecast[c][disease]["p90"][i] for c in clinic_ids), 1)})
    return {"history": history, "forecast": fcast}


def demand_series(snap: Snapshot, medicine_id: str, clinic_ids: list[str], weeks_hist: int = HIST_WEEKS, weeks_ahead: int = 8) -> dict:
    h = snap.hist
    history = [{"week": w.isoformat(), "value": float(sum(h.consumption[c][medicine_id][-weeks_hist + i] for c in clinic_ids))}
               for i, w in enumerate(h.weeks[-weeks_hist:])]
    first = snap.forecast[clinic_ids[0]][DISEASE_IDS[0]]["week_start"]
    fcast = [{"week": first[i],
              "median": round(sum(snap.demand[(c, medicine_id)]["weekly_central"][i] for c in clinic_ids), 1),
              "hi": round(sum(snap.demand[(c, medicine_id)]["weekly_high"][i] for c in clinic_ids), 1)}
             for i in range(weeks_ahead)]
    return {"history": history, "forecast": fcast}


def _risk_counts(rows) -> dict:
    counts = {r: 0 for r in RISK_ORDER}
    for r in rows:
        counts[r["risk"]] += 1
    return counts


def _at_risk(rows) -> list:
    return [r for r in rows if RISK_RANK[r["risk"]] >= RISK_RANK["moderate"]]


def disease_outlook(snap: Snapshot, clinic_ids: list[str]) -> dict:
    """Recent 4-week observed average vs forecast 4-week average, summed over the chosen clinics."""
    out = {}
    for d in DISEASE_IDS:
        recent = sum(float(snap.hist.cases[c][d][-4:].mean()) for c in clinic_ids)
        fut = sum(sum(snap.forecast[c][d]["median"][:4]) / 4.0 for c in clinic_ids)
        out[d] = {"name": DISEASE_NAME[d], "recent_weekly": round(recent, 1), "forecast_weekly": round(fut, 1),
                  "change_pct": round((fut / recent - 1) * 100, 1) if recent > 0 else None}
    return out


def kpis(snap: Snapshot) -> dict:
    rows = list(snap.rows.values())
    at_risk = _at_risk(rows)
    unacked = [a for a in snap.alerts if not a["acknowledged"]]
    repl = alerts_mod.replenishment(snap.rows, snap.today)
    return {
        "clinics_monitored": len(snap.clinics),
        "total_inventory_units": int(sum(r["usable_stock"] for r in rows)),
        "pairs_total": len(rows),
        "pairs_at_risk": len(at_risk),
        "clinics_at_risk": len({r["clinic_id"] for r in at_risk}),
        "pairs_by_risk": _risk_counts(rows),
        "active_alerts": len(unacked),
        "sms_eligible_alerts": sum(1 for a in unacked if a["sms_eligible"]),
        "redistribution_opportunities": len(snap.transfers["proposals"]),
        "redistribution_units": int(sum(p["quantity"] for p in snap.transfers["proposals"])),
        "replenishment_items": len(repl),
        "replenishment_units": int(sum(i["reorder_qty"] for i in repl)),
    }


def summary(snap: Snapshot, model_meta: dict | None) -> dict:
    ids = list(snap.clinics)
    risky = sorted(_at_risk(list(snap.rows.values())), key=lambda r: (-RISK_RANK[r["risk"]], r["days_to_stockout"]))[:6]
    top_risks = [{k: r[k] for k in ("clinic_id", "clinic_name", "short_name", "risk", "days_to_stockout", "reorder_qty", "unit", "order_by_date")}
                 for r in risky]
    region_rows = {}
    for region, label in REGIONS.items():
        rids = _ids(snap, region)
        pop = sum(snap.clinics[c]["catchment_pop"] for c in rids)
        per = {}
        for d in DISEASE_IDS:
            recent = sum(float(snap.hist.cases[c][d][-4:].mean()) for c in rids)
            fut = sum(sum(snap.forecast[c][d]["median"][:4]) / 4.0 for c in rids)
            per[d] = {"recent_per_1000": round(recent / (pop / 1000), 2), "forecast_per_1000": round(fut / (pop / 1000), 2)}
        region_rows[region] = {"label": label, "clinics": len(rids), "population": pop, "diseases": per}
    return {
        "data_label": DATA_LABEL, "as_of": snap.as_of.isoformat(), "today": snap.today.isoformat(),
        "scenario": snap.scenario, "kpis": kpis(snap), "disease_outlook": disease_outlook(snap, ids),
        "series": {"disease": {d: disease_series(snap, d, ids) for d in DISEASE_IDS},
                   "demand": {m.id: demand_series(snap, m.id, ids) for m in MEDICINES}},
        "regional_activity": region_rows,
        "top_risks": top_risks,
        "medicines": [{"id": m.id, "name": m.name, "short_name": m.short_name, "unit": m.unit} for m in MEDICINES],
        "diseases": [{"id": d.id, "name": d.name, "description": d.description} for d in DISEASES],
        "regions": [{"id": k, "label": v} for k, v in REGIONS.items()],
    }


def clinics_list(snap: Snapshot) -> list[dict]:
    out = []
    for cid, c in snap.clinics.items():
        rows = [snap.rows[(cid, m.id)] for m in MEDICINES]
        worst = max(rows, key=lambda r: (RISK_RANK[r["risk"]], -(r["days_to_stockout"] or 999)))
        soonest = min((r for r in rows if r["days_to_stockout"] is not None), key=lambda r: r["days_to_stockout"], default=None)
        out.append({
            "id": cid, "name": c["name"], "region": c["region"], "region_label": REGIONS[c["region"]],
            "lat": c["lat"], "lon": c["lon"], "catchment_pop": c["catchment_pop"], "lead_time_days": c["lead_time_days"],
            "health_worker": c["health_worker"], "phone": c["phone"],
            "worst_risk": worst["risk"], "pairs_by_risk": _risk_counts(rows),
            "soonest_stockout": None if soonest is None else {"short_name": soonest["short_name"], "days": soonest["days_to_stockout"], "risk": soonest["risk"]},
            "total_units": int(sum(r["usable_stock"] for r in rows)),
            "disease_outlook": disease_outlook(snap, [cid]),
            "nearby": nearby_clinics(snap.clinics, cid, 3),
        })
    return out


def inventory_rows(snap: Snapshot, clinic_id: str | None = None, medicine_id: str | None = None, risk: str | None = None) -> list[dict]:
    rows = [r for r in snap.rows.values()
            if (not clinic_id or r["clinic_id"] == clinic_id) and (not medicine_id or r["medicine_id"] == medicine_id)
            and (not risk or r["risk"] == risk)]
    return sorted(rows, key=lambda r: (-RISK_RANK[r["risk"]], r["days_to_stockout"] if r["days_to_stockout"] is not None else 1e9, r["clinic_id"], r["medicine_id"]))


def clinic_detail(snap: Snapshot, clinic_id: str) -> dict:
    info = next(c for c in clinics_list(snap) if c["id"] == clinic_id)
    trends = {d: disease_series(snap, d, [clinic_id], weeks_hist=52) for d in DISEASE_IDS}
    rows = inventory_rows(snap, clinic_id=clinic_id)
    proposals = [p for p in snap.transfers["proposals"] if clinic_id in (p["donor_id"], p["receiver_id"])]
    alerts = [a for a in snap.alerts if a["clinic_id"] == clinic_id]
    recs = [r["action"] for r in rows if RISK_RANK[r["risk"]] >= RISK_RANK["moderate"]]
    recs += [a["message"] for a in alerts if a["type"] == "surge"]
    for p in proposals:
        if p["receiver_id"] == clinic_id:
            recs.append(f"Ask {p['donor_name']} for {p['quantity']} {p['unit']} of {p['short_name']} ({p['road_km']:.0f} km, about {p['travel_hours']:.1f} h).")
        else:
            recs.append(f"May supply {p['quantity']} {p['unit']} of {p['short_name']} to {p['receiver_name']} without touching safety stock.")
    if not recs:
        recs = ["No action needed: stock is adequate for the forecast period."]
    return {"clinic": info, "inventory": rows, "trends": trends, "nearby": nearby_clinics(snap.clinics, clinic_id, 6),
            "transfers": proposals, "alerts": alerts, "recommendations": recs}


def forecast_view(snap: Snapshot, base: Snapshot, disease: str, region: str, weeks: int, model_meta: dict | None) -> dict:
    ids = _ids(snap, region)
    series = disease_series(snap, disease, ids, weeks_ahead=weeks)
    base_series = disease_series(base, disease, ids, weeks_ahead=weeks)
    meds = medicines_for_disease(disease)
    med_proj = []
    for m in meds:
        ds = demand_series(snap, m, ids, weeks_ahead=weeks)
        bs = demand_series(base, m, ids, weeks_ahead=weeks)
        rows = [snap.rows[(c, m)] for c in ids]
        med_proj.append({
            "medicine_id": m, "name": MEDICINE_BY_ID[m].name, "short_name": MEDICINE_BY_ID[m].short_name, "unit": MEDICINE_BY_ID[m].unit,
            "units_per_case": TREATMENT_PROTOCOL[m][disease], "series": ds, "baseline_forecast": bs["forecast"],
            "forecast_total": round(sum(p["median"] for p in ds["forecast"]), 0),
            "forecast_total_high": round(sum(p["hi"] for p in ds["forecast"]), 0),
            "stock_on_hand": int(sum(r["usable_stock"] for r in rows)),
            "clinics": [{"clinic_id": r["clinic_id"], "clinic_name": r["clinic_name"], "days_to_stockout": r["days_to_stockout"],
                         "risk": r["risk"], "reorder_qty": r["reorder_qty"], "usable_stock": r["usable_stock"]} for r in rows],
        })
    clinic_rows = []
    for c in ids:
        f = snap.forecast[c][disease]
        recent = float(snap.hist.cases[c][disease][-4:].mean())
        fut = sum(f["median"][:min(4, weeks)]) / min(4, weeks)
        clinic_rows.append({"clinic_id": c, "name": snap.clinics[c]["name"], "region": snap.clinics[c]["region"],
                            "recent_weekly": round(recent, 1), "forecast_weekly": round(fut, 1),
                            "change_pct": round((fut / recent - 1) * 100, 1) if recent > 0 else None,
                            "forecast": {k: v[:weeks] for k, v in f.items()}})
    rain = {}
    for region_id in REGIONS:
        rids = [c for c in snap.clinics if snap.clinics[c]["region"] == region_id]
        n = len(rids)
        rain[region_id] = {
            "scenario": [round(sum(snap.outlook[c]["rain_mm"][i] for c in rids) / n, 1) for i in range(8)],
            "climatology": [round(sum(snap.outlook[c]["climatology_mm"][i] for c in rids) / n, 1) for i in range(8)]}
    dm = (model_meta or {}).get("metrics", {}).get("diseases", {}).get(disease)
    return {
        "disease": disease, "disease_name": DISEASE_NAME[disease], "region": region, "weeks": weeks,
        "scenario": snap.scenario, "scenario_active": bool(snap.scenario.get("monsoon")),
        "series": series, "baseline_series": base_series, "clinics": clinic_rows, "medicines": med_proj,
        "weather_outlook": rain, "weeks_labels": [p["week"] for p in series["forecast"]],
        "drivers": ((model_meta or {}).get("importance") or {}).get(disease),
        "validation": dm, "data_label": DATA_LABEL,
    }


def walkthrough(base: Snapshot, scen: Snapshot) -> dict:
    ids = list(base.clinics)
    disease = {}
    for d in DISEASE_IDS:
        b4 = sum(sum(base.forecast[c][d]["median"][:4]) for c in ids)
        s4 = sum(sum(scen.forecast[c][d]["median"][:4]) for c in ids)
        b8 = sum(sum(base.forecast[c][d]["median"]) for c in ids)
        s8 = sum(sum(scen.forecast[c][d]["median"]) for c in ids)
        disease[d] = {
            "name": DISEASE_NAME[d], "baseline_4w": round(b4), "scenario_4w": round(s4),
            "pct_4w": round((s4 / b4 - 1) * 100, 1) if b4 else None,
            "baseline_8w": round(b8), "scenario_8w": round(s8), "pct_8w": round((s8 / b8 - 1) * 100, 1) if b8 else None,
            "series": disease_series(scen, d, ids), "baseline_series": disease_series(base, d, ids)}
    rain = {}
    for region_id, label in REGIONS.items():
        rids = [c for c in ids if base.clinics[c]["region"] == region_id]
        n = len(rids)
        rain[region_id] = {"label": label,
                           "scenario": [round(sum(scen.outlook[c]["rain_mm"][i] for c in rids) / n, 1) for i in range(8)],
                           "normal": [round(sum(base.outlook[c]["rain_mm"][i] for c in rids) / n, 1) for i in range(8)]}
    uplift = []
    for key, sr in scen.rows.items():
        br = base.rows[key]
        uplift.append({"clinic_id": key[0], "clinic_name": sr["clinic_name"], "medicine_id": key[1], "short_name": sr["short_name"], "unit": sr["unit"],
                       "baseline_daily": br["forecast_daily_usage_4w"], "scenario_daily": sr["forecast_daily_usage_4w"],
                       "pct": round((sr["forecast_daily_usage_4w"] / br["forecast_daily_usage_4w"] - 1) * 100, 1) if br["forecast_daily_usage_4w"] else None,
                       "baseline_risk": br["risk"], "scenario_risk": sr["risk"], "baseline_days": br["days_to_stockout"], "scenario_days": sr["days_to_stockout"],
                       "reorder_qty": sr["reorder_qty"]})
    top_uplift = sorted((u for u in uplift if u["pct"] is not None), key=lambda u: -(u["scenario_daily"] - u["baseline_daily"]))[:8]
    newly = [u for u in uplift if RISK_RANK[u["scenario_risk"]] >= RISK_RANK["moderate"] and RISK_RANK[u["baseline_risk"]] < RISK_RANK["moderate"]]
    sms = [a for a in scen.alerts if a["sms_eligible"]]
    return {
        "scenario": scen.scenario, "data_label": DATA_LABEL, "today": scen.today.isoformat(),
        "baseline_kpis": kpis(base), "scenario_kpis": kpis(scen),
        "disease": disease, "rain": rain, "demand_uplift": top_uplift,
        "at_risk": {"baseline": [u for u in uplift if RISK_RANK[u["baseline_risk"]] >= RISK_RANK["moderate"]],
                    "scenario": [u for u in uplift if RISK_RANK[u["scenario_risk"]] >= RISK_RANK["moderate"]],
                    "newly": newly},
        "sms": [{k: a[k] for k in ("key", "type", "severity", "clinic_id", "clinic_name", "health_worker", "to_number", "sms_body")} for a in sms[:12]],
        "sms_total": len(sms),
        "transfers": scen.transfers["proposals"], "surplus": scen.transfers["surplus"],
        "demand_series": {"act_baseline": demand_series(base, "act", ids), "act_scenario": demand_series(scen, "act", ids),
                          "ors_baseline": demand_series(base, "ors", ids), "ors_scenario": demand_series(scen, "ors", ids)},
    }
