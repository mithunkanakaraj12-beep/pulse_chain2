"""End-to-end check of the monsoon demonstration, driven through the real HTTP API.

    python scripts/e2e_check.py

Uses a throw-away database (and trains a throw-away model if none is configured), walks the eight demo steps
and fails loudly if any claim made by the demo is not backed by the numbers. Nothing is sent over the network.
"""
from __future__ import annotations

import dataclasses
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "backend"))

from pulsechain.api import create_app          # noqa: E402
from pulsechain.config import load_settings    # noqa: E402
from pulsechain.engine import Engine           # noqa: E402

results: list[tuple[bool, str]] = []


def check(ok: bool, text: str) -> None:
    results.append((bool(ok), text))
    print(("PASS  " if ok else "FAIL  ") + text)


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="pulsechain-e2e-"))
    settings = dataclasses.replace(load_settings(), db_path=tmp / "e2e.db", model_dir=tmp / "models",
                                   sms_mode="simulated", sms_allow_live=False, open_meteo_enabled=False)
    print("Preparing a throw-away database and model (about 30 s) ...")
    engine = Engine(settings)
    engine.reset()
    engine.train()
    client = create_app(settings).test_client()

    def get(path):
        r = client.get(path)
        assert r.status_code == 200, f"GET {path} -> {r.status_code} {r.data[:200]!r}"
        return r.get_json()

    print("\nStep 1 - normal conditions")
    s1 = get("/api/summary")
    check(not s1["scenario"]["monsoon"], "monsoon is off at start")
    check("SIMULATED" in s1["data_label"], "data is labelled as simulated")
    base_pairs = s1["kpis"]["pairs_at_risk"]
    base_sms = s1["kpis"]["sms_eligible_alerts"]
    print(f"      {base_pairs} pairs at risk, {base_sms} SMS-level alerts, {s1['kpis']['redistribution_opportunities']} transfers")

    print("\nStep 2 - simulated monsoon")
    check(client.post("/api/scenario", json={"monsoon": True}).status_code == 200, "monsoon scenario triggered")

    print("\nStep 3 - AI forecast")
    w = get("/api/demo/walkthrough")
    mal, chol = w["disease"]["malaria"], w["disease"]["cholera"]
    check(mal["pct_8w"] > 15, f"malaria 8-week forecast rises under monsoon ({mal['pct_8w']:+.1f}%)")
    check(chol["pct_8w"] > 100, f"cholera 8-week forecast rises sharply ({chol['pct_8w']:+.1f}%)")
    check(abs(w["disease"]["pneumonia"]["pct_8w"]) < 30, "pneumonia (weakly rain-related) changes little")
    fv = get("/api/forecast?disease=malaria&region=all&weeks=8")
    check(all(p["lo"] <= p["median"] <= p["hi"] for p in fv["series"]["forecast"]), "forecast intervals are ordered")
    check(fv["validation"]["mae_model"] < fv["validation"]["mae_seasonal_naive"], "model beats the seasonal-naive baseline on held-out data")

    print("\nStep 4 - inventory impact")
    up = w["demand_uplift"][0]
    check(up["pct"] > 20, f"largest demand jump: {up['short_name']} at {up['clinic_name']} ({up['pct']:+.0f}%)")

    print("\nStep 5 - shortage detection")
    s5 = get("/api/summary")
    check(s5["kpis"]["pairs_at_risk"] > base_pairs, f"pairs at risk rise {base_pairs} -> {s5['kpis']['pairs_at_risk']}")
    check(len(w["at_risk"]["newly"]) > 0, f"{len(w['at_risk']['newly'])} pairs newly at risk")

    print("\nStep 6 - SMS alerts (simulated)")
    alerts = get("/api/alerts")["alerts"]
    eligible = [a for a in alerts if a["sms_eligible"]]
    check(len(eligible) > base_sms, f"SMS-level alerts rise {base_sms} -> {len(eligible)}")
    r = client.post("/api/sms/send", json={"alert_key": eligible[0]["key"], "confirm_live": True})
    check(r.get_json()["status"] == "simulated", "SMS was simulated, not sent (live mode is off)")
    check(len(eligible[0]["sms_body"]) <= 320, "SMS text fits two segments")

    print("\nStep 7 - redistribution")
    t = get("/api/transfers")
    check(len(t["proposals"]) > 0, f"{len(t['proposals'])} transfer recommendations")
    inv = {(r["clinic_id"], r["medicine_id"]): r for r in get("/api/inventory")["rows"]}
    ok_safety = all(inv[(p["donor_id"], p["medicine_id"])]["usable_stock"] - p["quantity"] >= inv[(p["donor_id"], p["medicine_id"])]["safety_stock"] for p in t["proposals"])
    check(ok_safety, "no proposal drops a donor below its safety stock")
    check(all(b["shelf_days_at_arrival"] >= 90 for p in t["proposals"] for b in p["batches"]), "no proposal moves short-dated stock")
    p = t["proposals"][0]
    check(client.post("/api/transfers/decision", json={"key": p["key"], "decision": "approve"}).status_code == 200, "transfer approved by a human (simulated)")

    print("\nStep 8 - final dashboard")
    s8 = get("/api/summary")
    check(s8["kpis"]["pairs_at_risk"] <= s5["kpis"]["pairs_at_risk"], "approved transfer does not worsen risk")
    print(f"      pairs at risk: normal {base_pairs} -> monsoon {s5['kpis']['pairs_at_risk']} -> after approved transfer {s8['kpis']['pairs_at_risk']}")

    failed = [t for ok, t in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
