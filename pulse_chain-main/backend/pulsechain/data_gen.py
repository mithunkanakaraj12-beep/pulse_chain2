"""Synthetic dataset generator for the PulseChain AI demo.

EVERYTHING PRODUCED HERE IS SIMULATED. The generator encodes a small, documented set of causal
relationships so the forecasting pipeline has real structure to learn - and so the demo is internally
consistent - but it is an illustration, not epidemiology:

  rainfall -> (lag 2-5 wks, saturating, temperature-gated) -> malaria cases
  heavy rain / flooding -> (lag 1-2 wks, threshold) -> cholera cases
  cooler temperature -> pneumonia cases (weak rainfall effect)
  cases x treatment protocol + routine baseline -> weekly medicine consumption
  recent consumption x per-clinic "stock profile" -> current inventory (batches with expiry dates)

Cases follow a negative-binomial-like process with week-to-week persistence, so history is noisy and
over-dispersed like real surveillance counts. Monsoon onset is shifted randomly each year so the model
sees early and late seasons. Re-running with the same seed and as_of reproduces the dataset exactly.
"""
from __future__ import annotations

import math
import sqlite3
from datetime import date, datetime, timedelta, timezone

import numpy as np

from . import db
from .domain import (
    CLINICS, DATA_LABEL, DEFAULT_AS_OF, DISEASE_IDS, HISTORY_YEARS, MEDICINES, REGIONS,
    TREATMENT_PROTOCOL, demo_today, fictional_phone, week_starts,
)

WARMUP_WEEKS = 8

# Mean weekly rainfall (mm) by calendar month for a monsoon climate (illustrative).
_MONTH_MID_DOY = [15, 46, 74, 105, 135, 166, 196, 227, 258, 288, 319, 349]
_MONTH_RAIN = [3, 7, 9, 14, 28, 80, 125, 130, 105, 55, 12, 3]
_REGION_RAIN_FACTOR = {"highland": 0.9, "riverine": 1.0, "coastal": 1.15}
_REGION_TEMP_OFFSET = {"highland": -2.5, "riverine": 0.0, "coastal": 0.8}

# Stock cover (weeks of average usage on hand) by profile. Illustrative storyline:
#   normal  - routine well-managed stock
#   tight   - adequate overall but running lean on malaria commodities going into the season
#   surplus - hub clinics with large stocks that also keep a cholera / IV-fluid emergency reserve
_COVER_WEEKS = {"normal": (8.0, 11.0), "tight": (6.5, 8.0), "surplus": (16.0, 22.0)}
_COVER_OVERRIDES = {
    "tight": {"act": (4.2, 5.2), "rdt": (4.2, 5.2)},
    "surplus": {"ringer": (36.0, 48.0), "ors": (20.0, 26.0)},
}


def _rain_climatology(doy: np.ndarray) -> np.ndarray:
    d = np.asarray(doy, dtype=float)
    xs = [_MONTH_MID_DOY[-1] - 365] + _MONTH_MID_DOY + [_MONTH_MID_DOY[0] + 365]
    ys = [_MONTH_RAIN[-1]] + _MONTH_RAIN + [_MONTH_RAIN[0]]
    return np.interp(d, xs, ys)


def _nb(rng: np.random.Generator, mean: float, dispersion: float = 10.0) -> int:
    if mean <= 1e-9:
        return 0
    return int(rng.poisson(rng.gamma(dispersion, mean / dispersion)))


def _generate_weather(rng: np.random.Generator, weeks: list[date]) -> dict[str, dict[str, np.ndarray]]:
    """Per-region weekly weather with a shared monsoon-onset anomaly per year."""
    n = len(weeks)
    years = sorted({w.year for w in weeks})
    onset_shift = {y: int(rng.integers(-5, 5)) for y in years}  # weeks earlier(-)/later(+) than normal
    out: dict[str, dict[str, np.ndarray]] = {}
    for region in REGIONS:
        rain = np.zeros(n)
        temp = np.zeros(n)
        for i, wk in enumerate(weeks):
            doy = wk.timetuple().tm_yday
            shifted = (doy - 7 * onset_shift[wk.year]) % 365
            clim = float(_rain_climatology(np.array([shifted]))[0]) * _REGION_RAIN_FACTOR[region]
            if region == "coastal" and 250 <= doy <= 300:
                clim *= 1.3  # cyclone-season boost
            r = clim * rng.gamma(1.4, 1 / 1.4)
            if 120 <= doy <= 300 and rng.random() < 0.06:
                r += rng.uniform(100, 260)  # extreme rainfall event
            rain[i] = r
            t = 26 + 6 * math.cos(2 * math.pi * (doy - 140) / 365) + _REGION_TEMP_OFFSET[region]
            temp[i] = t + rng.normal(0, 1.0)
        out[region] = {"rain": rain, "temp": temp}
    return out


def _lagged_mean(a: np.ndarray, t: int, lo: int, hi: int) -> float:
    """Mean of a[t-hi .. t-lo] (inclusive), clipped at the start of the series."""
    start = max(0, t - hi)
    seg = a[start: max(start + 1, t - lo + 1)]
    return float(seg.mean())


def _simulate_cases(rng: np.random.Generator, pop: int, rain: np.ndarray, temp: np.ndarray,
                    clinic_factor: dict[str, float]) -> dict[str, np.ndarray]:
    n = len(rain)
    k = pop / 1000.0
    cases = {d: np.zeros(n, dtype=int) for d in DISEASE_IDS}
    for t in range(n):
        r25 = _lagged_mean(rain, t, 2, 5)
        r12 = _lagged_mean(rain, t, 1, 2)
        t4 = _lagged_mean(temp, t, 0, 3)
        s = 1.0 - math.exp(-r25 / 90.0)
        # malaria: saturating rainfall effect, gated by temperature suitability
        tfac = min(1.0, max(0.25, math.exp(-0.5 * ((t4 - 28.0) / 6.0) ** 2)))
        mal = k * 0.22 * (1 + 7.0 * s) * tfac * clinic_factor["malaria"]
        # cholera: threshold on heavy recent rain (flooding), warm temperatures
        s2 = max(0.0, (r12 - 60.0) / 100.0)
        chol = k * (0.015 + 0.85 * s2 ** 1.2) * (1.0 if t4 >= 24 else 0.6) * clinic_factor["cholera"]
        # pneumonia: cooler weather, weak rain effect
        cool = min(1.2, max(0.0, (28.0 - t4) / 8.0))
        pneu = k * (0.45 + 0.55 * cool + 0.10 * s) * clinic_factor["pneumonia"]
        for disease, driver, rho in (("malaria", mal, 0.35), ("cholera", chol, 0.40), ("pneumonia", pneu, 0.50)):
            prev = cases[disease][t - 1] if t > 0 else driver
            lam = (1 - rho) * driver + rho * prev
            cases[disease][t] = _nb(rng, lam)
    return cases


def _consumption(rng: np.random.Generator, pop: int, cases: dict[str, np.ndarray]) -> dict[str, np.ndarray]:
    n = len(next(iter(cases.values())))
    out: dict[str, np.ndarray] = {}
    for med in MEDICINES:
        base = med.baseline_weekly_per_1000 * pop / 1000.0
        use = np.full(n, base, dtype=float)
        for disease, rate in TREATMENT_PROTOCOL.get(med.id, {}).items():
            use += cases[disease] * rate
        use *= rng.lognormal(0.0, 0.08, size=n)
        out[med.id] = np.rint(use).astype(int)
    return out


def _special_batches(today: date) -> list[tuple[str, str, int, date, str]]:
    """Hand-placed batches that exercise expiry handling: (clinic, medicine, qty, expiry, note)."""
    return [
        ("C02", "act", 36, today - timedelta(days=21), "expired - still on shelf, must be excluded"),
        ("C03", "amox", 90, today + timedelta(days=25), "near expiry - too short-dated to transfer"),
        ("C07", "act", 80, today + timedelta(days=70), "short-dated but transferable"),
        ("C11", "ors", 120, today - timedelta(days=10), "expired - still on shelf, must be excluded"),
    ]


def generate(conn: sqlite3.Connection, as_of: date = DEFAULT_AS_OF, seed: int = 42,
             years: int = HISTORY_YEARS) -> dict:
    """Populate an initialised database with the synthetic dataset. Returns a summary dict."""
    if as_of.weekday() != 0:
        raise ValueError("as_of must be a Monday (weeks start on Monday)")
    rng = np.random.default_rng(seed)
    n_weeks = years * 52
    all_weeks = week_starts(as_of, n_weeks + WARMUP_WEEKS)
    weather = _generate_weather(rng, all_weeks)
    today = demo_today(as_of)

    weather_rows, case_rows, cons_rows, batch_rows, policy_rows, clinic_rows = [], [], [], [], [], []
    for idx, c in enumerate(CLINICS, start=1):
        clinic_rows.append((c.id, c.name, c.region, c.lat, c.lon, c.catchment_pop, c.lead_time_days,
                            c.road_speed_kmh, c.health_worker, fictional_phone(idx)))
        jitter = rng.lognormal(0.0, 0.15, size=len(all_weeks))
        rain = weather[c.region]["rain"] * jitter
        temp = weather[c.region]["temp"] + rng.normal(0, 0.4, size=len(all_weeks)) - np.minimum(3.0, rain * 0.012)
        factor = {d: float(rng.lognormal(0.0, 0.2)) for d in DISEASE_IDS}
        cases = _simulate_cases(rng, c.catchment_pop, rain, temp, factor)
        cons = _consumption(rng, c.catchment_pop, cases)
        keep = slice(WARMUP_WEEKS, None)
        weeks = all_weeks[keep]
        for i, wk in enumerate(weeks):
            weather_rows.append((c.id, wk.isoformat(), round(float(rain[keep][i]), 1), round(float(temp[keep][i]), 1)))
            for d in DISEASE_IDS:
                case_rows.append((c.id, d, wk.isoformat(), int(cases[d][keep][i])))
            for m in MEDICINES:
                cons_rows.append((c.id, m.id, wk.isoformat(), int(cons[m.id][keep][i])))

        # Current inventory: recent average usage x stock-profile cover, split into dated batches.
        for m in MEDICINES:
            lo, hi = _COVER_OVERRIDES.get(c.stock_profile, {}).get(m.id, _COVER_WEEKS[c.stock_profile])
            avg_week = float(np.mean(cons[m.id][-8:]))
            stock = int(round(avg_week * rng.uniform(lo, hi)))
            policy_rows.append((c.id, m.id, max(10, int(math.ceil(avg_week * 1.0)))))
            if stock <= 0:
                continue
            old_frac = float(rng.uniform(0.3, 0.5))
            old_qty = int(round(stock * old_frac))
            for qty, (dmin, dmax), recv in ((old_qty, (150, 400), (60, 150)),
                                            (stock - old_qty, (420, m.shelf_life_days - 60), (10, 60))):
                if qty <= 0:
                    continue
                expiry = today + timedelta(days=int(rng.integers(dmin, max(dmin + 1, dmax))))
                received = today - timedelta(days=int(rng.integers(*recv)))
                batch_rows.append((c.id, m.id, qty, expiry.isoformat(), received.isoformat(), "initial"))
    for clinic_id, med_id, qty, expiry, _note in _special_batches(today):
        batch_rows.append((clinic_id, med_id, qty, expiry.isoformat(), (today - timedelta(days=200)).isoformat(), "initial"))

    conn.executemany("INSERT INTO clinics VALUES (?,?,?,?,?,?,?,?,?,?)", clinic_rows)
    conn.executemany("INSERT INTO weather VALUES (?,?,?,?)", weather_rows)
    conn.executemany("INSERT INTO cases VALUES (?,?,?,?)", case_rows)
    conn.executemany("INSERT INTO consumption VALUES (?,?,?,?)", cons_rows)
    conn.executemany(
        "INSERT INTO inventory_batches(clinic_id, medicine_id, quantity, expiry_date, received_date, origin) VALUES (?,?,?,?,?,?)",
        batch_rows)
    conn.executemany("INSERT INTO inventory_policy VALUES (?,?,?)", policy_rows)

    db.set_meta(conn, "as_of", as_of.isoformat())
    db.set_meta(conn, "seed", seed)
    db.set_meta(conn, "history_weeks", n_weeks)
    db.set_meta(conn, "generated_at", datetime.now(timezone.utc).isoformat(timespec="seconds"))
    db.set_meta(conn, "data_label", DATA_LABEL)
    db.set_meta(conn, "scenario", {"monsoon": False, "intensity": 3.0, "regions": list(REGIONS), "weeks": 3})
    return {
        "clinics": len(clinic_rows), "weeks": n_weeks, "weather_rows": len(weather_rows),
        "case_rows": len(case_rows), "consumption_rows": len(cons_rows), "batches": len(batch_rows),
        "as_of": as_of.isoformat(), "seed": seed,
    }


def reset_demo(db_path, as_of: date = DEFAULT_AS_OF, seed: int = 42) -> dict:
    """Drop and recreate every table, then regenerate the dataset."""
    with db.session(db_path) as conn:
        db.drop_all(conn)
        db.init_schema(conn)
        return generate(conn, as_of=as_of, seed=seed)
