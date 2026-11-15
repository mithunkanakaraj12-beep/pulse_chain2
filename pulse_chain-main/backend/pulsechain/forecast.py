"""Disease forecasting: gradient-boosted models for weekly case counts, 1-8 weeks ahead.

Design (documented so it can be audited and replaced with validated data later)
------------------------------------------------------------------------------
* One model family per disease, trained on every (clinic, origin week, horizon) combination
  pooled across clinics. Pooling gives ~24k rows per disease, far more than a per-clinic fit.
* Target: log(1 + cases per 1,000 catchment population) at the target week.
* Features known at the forecast origin: recent case rates, rainfall/temperature in the weeks
  *before the target week* (rain effects are lagged - see data_gen), season, horizon, population.
  Rainfall/temperature for weeks after the origin come from a weather outlook: climatology by
  default, or a scenario (e.g. the simulated monsoon). Training uses observed weather, so
  real-world accuracy would also depend on the quality of the weather outlook.
* Three models per disease: mean (point forecast) plus 10th/90th percentile quantile models, which
  give an 80% prediction interval. Coverage is measured on a held-out final year.
* Baselines for comparison: seasonal naive (average of the same week in earlier years) and
  persistence (last observed week). Scikit-learn's HistGradientBoostingRegressor is used because it
  needs no extra dependency; XGBoost would be a drop-in replacement.

EVERYTHING HERE IS TRAINED ON SIMULATED DATA. Forecasts are illustrations of the method, not
epidemiological predictions.
"""
from __future__ import annotations

import json
import math
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

import joblib
import numpy as np
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.inspection import permutation_importance

from .domain import DATA_LABEL, DISEASE_IDS, FORECAST_MAX_WEEKS
from .history import History

HOLDOUT_WEEKS = 52
MIN_ORIGIN = 8
H_MAX = FORECAST_MAX_WEEKS

FEATURE_NAMES = [
    "horizon_weeks", "cases_last_week", "cases_prev_week", "cases_4wk_avg",
    "rain_lag1_2wk", "rain_lag2_5wk", "rain_prev_8wk", "temp_recent",
    "season_sin", "season_cos", "log_catchment_pop",
]
FEATURE_LABELS = {
    "horizon_weeks": "Forecast horizon",
    "cases_last_week": "Cases last week",
    "cases_prev_week": "Cases the week before",
    "cases_4wk_avg": "Average cases, last 4 weeks",
    "rain_lag1_2wk": "Rainfall 1-2 weeks earlier",
    "rain_lag2_5wk": "Rainfall 2-5 weeks earlier",
    "rain_prev_8wk": "Cumulative rainfall, prior 8 weeks",
    "temp_recent": "Recent temperature",
    "season_sin": "Season (sin)",
    "season_cos": "Season (cos)",
    "log_catchment_pop": "Catchment population",
}
FEATURE_GROUPS = {
    "Recent cases": ["cases_last_week", "cases_prev_week", "cases_4wk_avg"],
    "Rainfall": ["rain_lag1_2wk", "rain_lag2_5wk", "rain_prev_8wk"],
    "Temperature": ["temp_recent"],
    "Season": ["season_sin", "season_cos"],
    "Horizon & population": ["horizon_weeks", "log_catchment_pop"],
}

_HGB_PARAMS = dict(max_iter=260, learning_rate=0.06, max_leaf_nodes=24, min_samples_leaf=40,
                   l2_regularization=1.0)


# ----------------------------------------------------------------------------- weather outlook

def _climatology(arr: np.ndarray, n_future: int) -> np.ndarray:
    """Extend `arr` by `n_future` weeks using the mean of the same week (+/-1) in earlier years."""
    n = len(arr)
    out = np.concatenate([arr, np.zeros(n_future)])
    for i in range(n, n + n_future):
        vals = []
        for j in range(1, 6):
            for d in (-1, 0, 1):
                k = i - 52 * j + d
                if 0 <= k < n:
                    vals.append(arr[k])
        out[i] = float(np.mean(vals)) if vals else float(arr[-1])
    return out


def default_scenario() -> dict:
    return {"monsoon": False, "intensity": 3.0, "regions": ["highland", "riverine", "coastal"], "weeks": 3}


def _scenario_factor(k: int, scenario: dict) -> float:
    """Rainfall multiplier for forecast week k (1-based): ramps up over 2 weeks, holds, then decays over 3."""
    intensity = float(scenario["intensity"])
    duration = int(scenario["weeks"])
    if k <= duration:
        return 1.0 + (intensity - 1.0) * min(1.0, k / 2.0)
    return 1.0 + (intensity - 1.0) * max(0.0, 1.0 - (k - duration) / 3.0)


def weather_outlook(hist: History, clinic_id: str, scenario: dict | None) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Return (rain_ext, temp_ext, rain_climatology_future) for one clinic, history + H_MAX future weeks."""
    n = hist.n
    rain = _climatology(hist.rain[clinic_id], H_MAX)
    temp = _climatology(hist.temp[clinic_id], H_MAX)
    clim_future = rain[n:].copy()
    if scenario and scenario.get("monsoon") and hist.clinics[clinic_id]["region"] in scenario.get("regions", []):
        for k in range(1, H_MAX + 1):
            i = n - 1 + k
            before = rain[i]
            rain[i] = before * _scenario_factor(k, scenario)
            temp[i] -= min(3.0, (rain[i] - before) * 0.012)   # heavy rain cools, as in the generator
    return rain, temp, clim_future


# ----------------------------------------------------------------------------- features

def _weather_feats(rain: np.ndarray, temp: np.ndarray, dates: list[date], t: int, h: int) -> list[float]:
    w = t + h
    doy = dates[w].timetuple().tm_yday
    ang = 2 * math.pi * doy / 365.25
    return [
        float(rain[w - 2:w].mean()),       # rain_lag1_2wk
        float(rain[w - 5:w - 1].mean()),   # rain_lag2_5wk
        float(rain[w - 8:w].mean()),       # rain_prev_8wk
        float(temp[w - 3:w + 1].mean()),   # temp_recent
        math.sin(ang), math.cos(ang),
    ]


def _row(rate: np.ndarray, weather: list[float], t: int, h: int, pop: int) -> list[float]:
    wf = weather
    return [
        float(h),
        math.log1p(rate[t]), math.log1p(rate[t - 1]), math.log1p(float(rate[t - 3:t + 1].mean())),
        wf[0], wf[1], wf[2], wf[3], wf[4], wf[5],
        math.log(pop),
    ]


def _all_dates(hist: History) -> list[date]:
    return hist.weeks + [hist.weeks[-1] + timedelta(weeks=k) for k in range(1, H_MAX + 1)]


def _build_training(hist: History, disease: str):
    """Rows for every clinic / origin / horizon whose target week is observed."""
    dates = _all_dates(hist)
    X, y, meta = [], [], []
    n = hist.n
    for ci, (cid, c) in enumerate(hist.clinics.items()):
        pop = c["catchment_pop"]
        cases = hist.cases[cid][disease]
        rate = cases / (pop / 1000.0)
        rain, temp = hist.rain[cid], hist.temp[cid]
        for t in range(MIN_ORIGIN, n - 1):
            for h in range(1, min(H_MAX, n - 1 - t) + 1):
                w = t + h
                X.append(_row(rate, _weather_feats(rain, temp, dates, t, h), t, h, pop))
                y.append(math.log1p(rate[w]))
                meta.append((ci, t, w, h))
    return np.asarray(X), np.asarray(y), np.asarray(meta, dtype=int)


def _fit(X, y, seed: int, quantile: float | None = None) -> HistGradientBoostingRegressor:
    params = dict(_HGB_PARAMS, random_state=seed)
    if quantile is not None:
        params.update(loss="quantile", quantile=quantile)
    return HistGradientBoostingRegressor(**params).fit(X, y)


def _calibrate_scale(pred, lo, hi, actual, target: float = 0.80) -> float:
    """Smallest widening factor (>=1) for which the band covers `target` of the calibration actuals."""
    for scale in np.arange(1.0, 4.01, 0.1):
        l, h = pred - scale * (pred - lo), pred + scale * (hi - pred)
        if np.mean((actual >= l) & (actual <= h)) >= target:
            return round(float(scale), 1)
    return 4.0


def _to_cases(log_rate: np.ndarray, pop: np.ndarray) -> np.ndarray:
    return np.clip(np.expm1(log_rate), 0.0, None) * pop / 1000.0


# ----------------------------------------------------------------------------- training

def train(hist: History, model_dir: Path, seed: int = 42,
          progress: Callable[[str], None] = lambda _m: None) -> dict:
    """Train, validate on the final year, refit on all data, and persist models + metrics."""
    model_dir = Path(model_dir)
    model_dir.mkdir(parents=True, exist_ok=True)
    n = hist.n
    holdout_start = n - HOLDOUT_WEEKS
    clinic_ids = list(hist.clinics)
    pops = np.array([hist.clinics[c]["catchment_pop"] for c in clinic_ids], dtype=float)
    rng = np.random.default_rng(seed)

    bundle: dict[str, dict] = {}
    metrics: dict = {"diseases": {}, "holdout_weeks": HOLDOUT_WEEKS}
    importance: dict = {}

    for disease in DISEASE_IDS:
        progress(f"  training {disease} ...")
        X, y, meta = _build_training(hist, disease)
        w = meta[:, 2]
        train_mask, test_mask = w < holdout_start, w >= holdout_start
        Xtr, ytr, Xte, yte = X[train_mask], y[train_mask], X[test_mask], y[test_mask]
        m_mean = _fit(Xtr, ytr, seed)
        m_lo = _fit(Xtr, ytr, seed, 0.10)
        m_hi = _fit(Xtr, ytr, seed, 0.90)

        te_meta = meta[test_mask]
        te_pop = pops[te_meta[:, 0]]
        pred = _to_cases(m_mean.predict(Xte), te_pop)
        lo = np.minimum(_to_cases(m_lo.predict(Xte), te_pop), pred)
        hi = np.maximum(_to_cases(m_hi.predict(Xte), te_pop), pred)
        actual = np.array([hist.cases[clinic_ids[ci]][disease][wi] for ci, _t, wi, _h in te_meta])
        persist = np.array([hist.cases[clinic_ids[ci]][disease][t] for ci, t, _wi, _h in te_meta])
        seasonal = np.array([
            np.mean([hist.cases[clinic_ids[ci]][disease][wi - 52 * j] for j in range(1, 6) if wi - 52 * j >= 0])
            for ci, _t, wi, _h in te_meta])

        # Interval calibration: raw quantile models under-cover, so widen the band by a single factor chosen on
        # the first half of the holdout year and report coverage on the untouched second half.
        half = te_meta[:, 2] < holdout_start + HOLDOUT_WEEKS // 2
        scale = _calibrate_scale(pred[half], lo[half], hi[half], actual[half])
        lo_c, hi_c = pred - scale * (pred - lo), pred + scale * (hi - pred)
        ev = ~half

        def mae(p): return float(np.mean(np.abs(p - actual)))
        mm, ms, mp = mae(pred), mae(seasonal), mae(persist)
        per_h = {}
        for h in range(1, H_MAX + 1):
            sel = te_meta[:, 3] == h
            if sel.any():
                per_h[str(h)] = {
                    "model": float(np.mean(np.abs(pred[sel] - actual[sel]))),
                    "seasonal_naive": float(np.mean(np.abs(seasonal[sel] - actual[sel]))),
                    "persistence": float(np.mean(np.abs(persist[sel] - actual[sel]))),
                }
        metrics["diseases"][disease] = {
            "rows_train": int(train_mask.sum()), "rows_holdout": int(test_mask.sum()),
            "mean_weekly_cases_holdout": float(actual.mean()),
            "mae_model": mm, "mae_seasonal_naive": ms, "mae_persistence": mp,
            "skill_vs_seasonal_naive": 1 - mm / ms if ms else None,
            "skill_vs_persistence": 1 - mm / mp if mp else None,
            "interval_coverage_80_raw": float(np.mean((actual >= lo) & (actual <= hi))),
            "interval_scale": scale,
            "interval_coverage_80": float(np.mean((actual[ev] >= lo_c[ev]) & (actual[ev] <= hi_c[ev]))),
            "interval_mean_width": float(np.mean((hi_c - lo_c)[ev])),
            "per_horizon_mae": per_h,
        }

        # Permutation importance of the point model on a held-out sample (log-rate MAE).
        sample = rng.choice(len(Xte), size=min(3000, len(Xte)), replace=False)
        pi = permutation_importance(m_mean, Xte[sample], yte[sample], n_repeats=3, random_state=seed,
                                    scoring="neg_mean_absolute_error")
        importance[disease] = {f: float(max(0.0, v)) for f, v in zip(FEATURE_NAMES, pi.importances_mean)}

        progress(f"  refitting {disease} on all data ...")
        bundle[disease] = {"median": _fit(X, y, seed), "p10": _fit(X, y, seed, 0.10), "p90": _fit(X, y, seed, 0.90),
                           "interval_scale": scale}

    grouped = {}
    for disease, imp in importance.items():
        total = sum(imp.values()) or 1.0
        grouped[disease] = {
            "groups": sorted(({"group": g, "share": sum(imp[f] for f in feats) / total}
                              for g, feats in FEATURE_GROUPS.items()), key=lambda r: -r["share"]),
            "features": sorted(({"feature": f, "label": FEATURE_LABELS[f], "share": imp[f] / total}
                                for f in FEATURE_NAMES), key=lambda r: -r["share"]),
        }
    meta_out = {
        "trained_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "as_of": hist.as_of.isoformat(), "seed": seed, "feature_names": FEATURE_NAMES,
        "algorithm": "HistGradientBoostingRegressor (mean + 10th/90th percentile quantile models)",
        "data_label": DATA_LABEL, "metrics": metrics, "importance": grouped,
        "notes": [
            "Prediction bands are the raw 10th-90th percentile models widened by a factor calibrated on the first half of the holdout year; coverage is reported on the second half.",
            "Trained on SIMULATED data; metrics describe the method on this synthetic dataset only.",
            "Training uses observed weather; at forecast time future weather is an outlook (climatology or scenario).",
            "Holdout = targets in the final 52 weeks; reported models are refit on all data for deployment.",
        ],
    }
    joblib.dump(bundle, model_dir / "models.joblib")
    (model_dir / "metrics.json").write_text(json.dumps(meta_out, indent=2), encoding="utf-8")
    return meta_out


def models_ready(model_dir: Path) -> bool:
    model_dir = Path(model_dir)
    return (model_dir / "models.joblib").is_file() and (model_dir / "metrics.json").is_file()


def load_metrics(model_dir: Path) -> dict | None:
    p = Path(model_dir) / "metrics.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.is_file() else None


# ----------------------------------------------------------------------------- inference

class Forecaster:
    """Loads persisted models and produces clinic-level forecasts for a given weather scenario."""

    def __init__(self, model_dir: Path):
        self.model_dir = Path(model_dir)
        self._bundle = joblib.load(self.model_dir / "models.joblib")

    def forecast(self, hist: History, scenario: dict | None = None) -> dict:
        """-> {clinic_id: {disease: {week_start, p10, median, p90}}} for H_MAX weeks starting on the demo 'today'."""
        dates = _all_dates(hist)
        n = hist.n
        t = n - 1
        future_weeks = [dates[n - 1 + k].isoformat() for k in range(1, H_MAX + 1)]
        out: dict = {}
        for cid, c in hist.clinics.items():
            pop = c["catchment_pop"]
            rain, temp, _ = weather_outlook(hist, cid, scenario)
            weather_by_h = [_weather_feats(rain, temp, dates, t, h) for h in range(1, H_MAX + 1)]
            out[cid] = {}
            for disease in DISEASE_IDS:
                rate = hist.cases[cid][disease] / (pop / 1000.0)
                X = np.asarray([_row(rate, weather_by_h[h - 1], t, h, pop) for h in range(1, H_MAX + 1)])
                mdl = self._bundle[disease]
                pops = np.full(H_MAX, float(pop))
                med = _to_cases(mdl["median"].predict(X), pops)
                lo = np.minimum(_to_cases(mdl["p10"].predict(X), pops), med)
                hi = np.maximum(_to_cases(mdl["p90"].predict(X), pops), med)
                sc = float(mdl.get("interval_scale", 1.0))
                lo, hi = np.clip(med - sc * (med - lo), 0.0, None), med + sc * (hi - med)
                out[cid][disease] = {
                    "week_start": future_weeks,
                    "p10": [round(float(v), 1) for v in lo],
                    "median": [round(float(v), 1) for v in med],
                    "p90": [round(float(v), 1) for v in hi],
                }
        return out

    def outlook(self, hist: History, scenario: dict | None) -> dict:
        """Weekly rainfall outlook per clinic: scenario vs climatology (for the demo chart)."""
        res = {}
        for cid in hist.clinics:
            rain, _temp, clim = weather_outlook(hist, cid, scenario)
            res[cid] = {"rain_mm": [round(float(v), 1) for v in rain[hist.n:]],
                        "climatology_mm": [round(float(v), 1) for v in clim]}
        return res
