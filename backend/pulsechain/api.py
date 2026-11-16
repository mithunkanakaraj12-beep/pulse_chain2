"""Flask REST API + static PWA hosting.

Run with:  python -m pulsechain.cli serve      (see README for the full route list)
Every route returns JSON; errors are {"error": "<message>"} with a suitable status code.
"""
from __future__ import annotations

import logging
from datetime import date, datetime

import numpy as np
from flask import Flask, jsonify, request, send_from_directory
from flask.json.provider import DefaultJSONProvider
from werkzeug.exceptions import HTTPException

from . import db, forecast as fc, reports, sms as sms_mod, weather
from .config import Settings, load_settings
from .domain import DATA_LABEL, DISEASE_IDS, FORECAST_MAX_WEEKS, MEDICINES, REGIONS
from .engine import ApiError, Engine
from .inventory import RISK_ORDER

log = logging.getLogger("pulsechain")


class _Provider(DefaultJSONProvider):
    @staticmethod
    def default(o):
        if isinstance(o, np.integer):
            return int(o)
        if isinstance(o, np.floating):
            return float(o)
        if isinstance(o, np.ndarray):
            return o.tolist()
        if isinstance(o, (date, datetime)):
            return o.isoformat()
        return DefaultJSONProvider.default(o)


def _body() -> dict:
    data = request.get_json(silent=True)
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ApiError(400, "Request body must be a JSON object.")
    return data


def _choice(value, name: str, allowed, default=None):
    value = default if value in (None, "") else value
    if value not in allowed:
        raise ApiError(400, f"'{name}' must be one of: {', '.join(sorted(allowed))}.")
    return value


def create_app(settings: Settings | None = None) -> Flask:
    settings = settings or load_settings()
    engine = Engine(settings)
    app = Flask(__name__, static_folder=None)
    app.json = _Provider(app)
    app.config["ENGINE"] = engine

    @app.errorhandler(ApiError)
    def _api_error(e: ApiError):
        return jsonify({"error": e.message}), e.status

    @app.errorhandler(404)
    def _not_found(_e):
        if request.path.startswith("/api/") or not (settings.frontend_dir / "index.html").is_file():
            return jsonify({"error": "Not found."}), 404
        return send_from_directory(settings.frontend_dir, "index.html")

    @app.errorhandler(405)
    def _bad_method(_e):
        return jsonify({"error": "Method not allowed."}), 405

    @app.errorhandler(Exception)
    def _unhandled(e):
        if isinstance(e, HTTPException):
            return jsonify({"error": e.description}), e.code
        log.exception("Unhandled error")
        return jsonify({"error": "Internal server error."}), 500

    def need_ready():
        if not engine.has_data():
            raise ApiError(503, "No data yet. Run: python -m pulsechain.cli reset --train")
        if not engine.model_ready():
            raise ApiError(503, "Forecast model not trained. Run: python -m pulsechain.cli train")

    def model_meta():
        return fc.load_metrics(settings.model_dir)

    # ----------------------------------------------------------------- meta
    @app.get("/api/health")
    def health():
        return jsonify({"status": "ok", "data_ready": engine.has_data(), "model_ready": engine.model_ready()})

    @app.get("/api/meta")
    def meta():
        need_ready()
        m = engine.meta()
        mm = model_meta() or {}
        snap = engine.snapshot()
        return jsonify({
            **m, "data_label": DATA_LABEL, "today": snap.today.isoformat(), "scenario": snap.scenario,
            "sms_mode": settings.sms_mode, "live_sms_possible": settings.live_sms_possible,
            "alert_risk_threshold": settings.alert_risk_threshold, "open_meteo_enabled": settings.open_meteo_enabled,
            "model_trained_at": mm.get("trained_at"), "forecast_weeks_max": FORECAST_MAX_WEEKS,
            "regions": [{"id": k, "label": v} for k, v in REGIONS.items()],
            "diseases": [{"id": d, "name": d.title()} for d in DISEASE_IDS],
            "medicines": [{"id": x.id, "name": x.name, "short_name": x.short_name, "unit": x.unit} for x in MEDICINES],
            "risk_levels": RISK_ORDER,
        })

    @app.get("/api/model")
    def model():
        need_ready()
        return jsonify(model_meta())

    # ----------------------------------------------------------------- dashboard & clinics
    @app.get("/api/summary")
    def summary():
        need_ready()
        return jsonify(reports.summary(engine.snapshot(), model_meta()))

    @app.get("/api/clinics")
    def clinics():
        need_ready()
        return jsonify({"clinics": reports.clinics_list(engine.snapshot())})

    @app.get("/api/clinics/<clinic_id>")
    def clinic(clinic_id: str):
        need_ready()
        snap = engine.snapshot()
        if clinic_id not in snap.clinics:
            raise ApiError(404, f"Clinic {clinic_id} not found.")
        return jsonify(reports.clinic_detail(snap, clinic_id))

    # ----------------------------------------------------------------- forecasting
    @app.get("/api/forecast")
    def forecast():
        need_ready()
        disease = _choice(request.args.get("disease"), "disease", set(DISEASE_IDS), "malaria")
        region = _choice(request.args.get("region"), "region", set(REGIONS) | {"all"}, "all")
        try:
            weeks = int(request.args.get("weeks", 6))
        except ValueError:
            raise ApiError(400, "'weeks' must be a whole number.")
        if not 2 <= weeks <= FORECAST_MAX_WEEKS:
            raise ApiError(400, f"'weeks' must be between 2 and {FORECAST_MAX_WEEKS}.")
        return jsonify(reports.forecast_view(engine.snapshot(), engine.baseline_snapshot(), disease, region, weeks, model_meta()))

    @app.get("/api/scenario")
    def get_scenario():
        need_ready()
        return jsonify(engine.scenario())

    @app.post("/api/scenario")
    def set_scenario():
        need_ready()
        return jsonify(engine.set_scenario(_body()))

    @app.get("/api/demo/walkthrough")
    def walkthrough():
        need_ready()
        sc = engine.scenario()
        sc["monsoon"] = True
        return jsonify(reports.walkthrough(engine.baseline_snapshot(), engine.snapshot(sc)))

    # ----------------------------------------------------------------- inventory
    @app.get("/api/inventory")
    def inventory():
        need_ready()
        risk = request.args.get("risk") or None
        if risk:
            _choice(risk, "risk", set(RISK_ORDER))
        snap = engine.snapshot()
        cid, mid = request.args.get("clinic_id") or None, request.args.get("medicine_id") or None
        if cid and cid not in snap.clinics:
            raise ApiError(404, f"Clinic {cid} not found.")
        if mid and mid not in {m.id for m in MEDICINES}:
            raise ApiError(404, f"Medicine {mid} not found.")
        return jsonify({"rows": reports.inventory_rows(snap, cid, mid, risk), "today": snap.today.isoformat()})

    @app.patch("/api/inventory/batches/<int:batch_id>")
    def patch_batch(batch_id: int):
        need_ready()
        data = _body()
        if "quantity" not in data:
            raise ApiError(400, "'quantity' is required.")
        return jsonify(engine.set_batch_quantity(batch_id, data["quantity"]))

    @app.post("/api/inventory/batches")
    def add_batch():
        need_ready()
        d = _body()
        for k in ("clinic_id", "medicine_id", "quantity", "expiry_date"):
            if k not in d:
                raise ApiError(400, f"'{k}' is required.")
        return jsonify(engine.add_batch(d["clinic_id"], d["medicine_id"], d["quantity"], d["expiry_date"])), 201

    # ----------------------------------------------------------------- redistribution
    @app.get("/api/transfers")
    def transfers():
        need_ready()
        snap = engine.snapshot()
        return jsonify({**snap.transfers, "history": engine.transfer_history(), "approval_required": True,
                        "clinics": [{"id": c["id"], "name": c["name"], "lat": c["lat"], "lon": c["lon"], "region": c["region"],
                                     "catchment_pop": c["catchment_pop"]} for c in snap.clinics.values()],
                        "note": "Transfers are recommendations. Approving one here only updates the simulated demo inventory."})

    @app.post("/api/transfers/decision")
    def transfer_decision():
        need_ready()
        d = _body()
        if not isinstance(d.get("key"), str):
            raise ApiError(400, "'key' (the proposal key) is required.")
        return jsonify(engine.decide_transfer(d["key"], d.get("decision"), d.get("note")))

    # ----------------------------------------------------------------- alerts & SMS
    @app.get("/api/alerts")
    def alerts():
        need_ready()
        snap = engine.snapshot()
        from . import alerts as alerts_mod
        return jsonify({"alerts": snap.alerts, "replenishment": alerts_mod.replenishment(snap.rows, snap.today),
                        "threshold": settings.alert_risk_threshold, "sms_mode": settings.sms_mode,
                        "sms_log": engine.sms_log(30)})

    @app.post("/api/alerts/ack")
    def ack():
        need_ready()
        d = _body()
        if not isinstance(d.get("key"), str):
            raise ApiError(400, "'key' is required.")
        return jsonify(engine.acknowledge(d["key"]))

    @app.post("/api/sms/send")
    def sms_send():
        need_ready()
        d = _body()
        key = d.get("alert_key")
        if not isinstance(key, str):
            raise ApiError(400, "'alert_key' is required.")
        alert = next((a for a in engine.snapshot().alerts if a["key"] == key), None)
        if alert is None:
            raise ApiError(404, "Alert not found.")
        if not alert["sms_eligible"]:
            raise ApiError(400, "This alert is below the SMS risk threshold.")
        with db.session(settings.db_path) as conn:
            result = sms_mod.send_alert_sms(settings, conn, alert, confirm_live=d.get("confirm_live") is True)
        return jsonify(result), (200 if result["status"] in {"simulated", "sent"} else 409)

    @app.get("/api/sms/log")
    def sms_log():
        need_ready()
        return jsonify({"log": engine.sms_log(100), "mode": settings.sms_mode})

    # ----------------------------------------------------------------- weather & admin
    @app.get("/api/weather/live")
    def live_weather():
        need_ready()
        cid = request.args.get("clinic_id", "C01")
        snap = engine.snapshot()
        if cid not in snap.clinics:
            raise ApiError(404, f"Clinic {cid} not found.")
        c = snap.clinics[cid]
        return jsonify({"clinic_id": cid, **weather.fetch_live_outlook(c["lat"], c["lon"], settings.open_meteo_enabled,
                                                                      settings.open_meteo_timeout)})

    @app.post("/api/admin/reset")
    def reset():
        if not engine.model_ready():
            raise ApiError(503, "Forecast model not trained. Run: python -m pulsechain.cli train")
        engine.reset()
        return jsonify({"status": "reset", "meta": engine.meta()})

    # ----------------------------------------------------------------- static PWA
    @app.get("/")
    def index():
        return send_from_directory(settings.frontend_dir, "index.html")

    @app.get("/<path:path>")
    def static_files(path: str):
        if path.startswith("api/"):
            raise ApiError(404, "Unknown API route.")
        full = settings.frontend_dir / path
        if not full.is_file():
            return send_from_directory(settings.frontend_dir, "index.html")
        resp = send_from_directory(settings.frontend_dir, path)
        if path == "sw.js":
            resp.headers["Cache-Control"] = "no-cache"
            resp.headers["Service-Worker-Allowed"] = "/"
        return resp

    return app
