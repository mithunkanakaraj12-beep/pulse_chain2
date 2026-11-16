import unittest
from datetime import date, timedelta

import numpy as np

from pulsechain.inventory import (assess_pair, daily_demand, risk_from_margin, simulate_stock, weekly_medicine_demand)

TODAY = date(2026, 5, 25)
CLINIC = {"id": "T1", "name": "Test Clinic", "region": "highland", "lead_time_days": 14}


def row(batches, demand_per_day, safety=0, medicine="act", lead=14):
    d = np.full(180, float(demand_per_day))
    bl = [{"id": i, "quantity": q, "expiry_date": (TODAY + timedelta(days=e)).isoformat()} for i, (q, e) in enumerate(batches)]
    return assess_pair(clinic={**CLINIC, "lead_time_days": lead}, medicine_id=medicine, batches=bl, safety=safety, today=TODAY,
                       demand_central=d, demand_high=d * 1.5, recent_avg_weekly=demand_per_day * 7)


class SimulationTests(unittest.TestCase):
    def test_stockout_day_matches_arithmetic(self):
        res = simulate_stock([(TODAY + timedelta(days=400), 100)], np.full(180, 10.0), TODAY, safety=0)
        self.assertAlmostEqual(res.days_to_stockout, 10.0, places=1)

    def test_safety_stock_crossing(self):
        res = simulate_stock([(TODAY + timedelta(days=400), 100)], np.full(180, 10.0), TODAY, safety=30)
        self.assertAlmostEqual(res.days_to_safety, 7.0, places=1)

    def test_expired_batches_are_never_counted(self):
        res = simulate_stock([(TODAY - timedelta(days=1), 500), (TODAY + timedelta(days=400), 20)], np.full(180, 10.0), TODAY, 0)
        self.assertAlmostEqual(res.days_to_stockout, 2.0, places=1)

    def test_stock_expiring_before_use_counts_as_waste_not_cover(self):
        res = simulate_stock([(TODAY + timedelta(days=5), 100)], np.full(180, 1.0), TODAY, 0)
        self.assertGreater(res.wasted_units, 90)
        self.assertLess(res.days_to_stockout, 10)

    def test_no_demand_never_stocks_out(self):
        res = simulate_stock([(TODAY + timedelta(days=400), 50)], np.zeros(180), TODAY, 0)
        self.assertIsNone(res.days_to_stockout)

    def test_empty_stock_is_immediate_stockout(self):
        self.assertEqual(simulate_stock([], np.full(180, 5.0), TODAY, 0).days_to_stockout, 0.0)


class RiskAndReorderTests(unittest.TestCase):
    def test_risk_boundaries(self):
        self.assertEqual(risk_from_margin(-0.1), "critical")
        self.assertEqual(risk_from_margin(0), "high")
        self.assertEqual(risk_from_margin(9.9), "high")
        self.assertEqual(risk_from_margin(10), "moderate")
        self.assertEqual(risk_from_margin(20.9), "moderate")
        self.assertEqual(risk_from_margin(21), "low")
        self.assertEqual(risk_from_margin(None), "low")

    def test_critical_when_stock_ends_before_delivery(self):
        r = row([(100, 400)], 10, lead=21)           # 10 days of stock, 21-day delivery
        self.assertEqual(r["risk"], "critical")
        self.assertTrue(r["order_now"])

    def test_adequate_stock_is_low_risk_with_no_reorder(self):
        r = row([(5000, 600)], 10, safety=50, lead=14)
        self.assertEqual(r["risk"], "low")
        self.assertEqual(r["reorder_qty"], 0)

    def test_reorder_quantity_formula_and_rounding(self):
        # demand over lead(14)+28 = 42 days x 10 = 420, + safety 50, - usable 100 = 370 -> round up to multiple of 10 (ACT)
        r = row([(100, 600)], 10, safety=50, lead=14)
        self.assertEqual(r["reorder_qty"], 370)
        r2 = row([(100, 600)], 10, safety=55, lead=14)   # 375 -> 380
        self.assertEqual(r2["reorder_qty"], 380)

    def test_expired_units_are_reported_and_excluded(self):
        r = row([(200, -10), (100, 600)], 5)
        self.assertEqual(r["usable_stock"], 100)
        self.assertEqual(r["expired_units"], 200)

    def test_high_demand_case_stocks_out_sooner(self):
        r = row([(300, 600)], 10)
        self.assertLess(r["days_to_stockout_high_demand"], r["days_to_stockout"])


class DemandTests(unittest.TestCase):
    def test_cases_translate_to_medicine_units(self):
        # ORS: 9 sachets per cholera case + baseline 5 per 1000 people per week
        d = weekly_medicine_demand("ors", 10000, {"malaria": [0, 0], "cholera": [10, 20], "pneumonia": [0, 0]})
        self.assertAlmostEqual(d[0], 5.0 * 10 + 9 * 10)
        self.assertAlmostEqual(d[1], 5.0 * 10 + 9 * 20)

    def test_daily_demand_repeats_last_week_beyond_forecast(self):
        d = daily_demand([70.0, 140.0], days=30)
        self.assertAlmostEqual(d[0], 10.0)
        self.assertAlmostEqual(d[7], 20.0)
        self.assertAlmostEqual(d[29], 20.0)


if __name__ == "__main__":
    unittest.main()
