import unittest
from datetime import date, timedelta

import numpy as np

from pulsechain.inventory import assess_pair
from pulsechain.redistribution import MAX_ROAD_KM, MIN_SHELF_DAYS_AT_ARRIVAL, propose_transfers

TODAY = date(2026, 5, 25)


def clinic(cid, lat, lon, lead=10, speed=30.0):
    return {"id": cid, "name": f"Clinic {cid}", "region": "highland", "lat": lat, "lon": lon, "catchment_pop": 10000,
            "lead_time_days": lead, "road_speed_kmh": speed}


def make(clinics, stock):
    """stock: {clinic_id: (daily_demand, safety, [(qty, expiry_days)])} -> rows, demand for medicine 'act'."""
    rows, demand = {}, {}
    for cid, c in clinics.items():
        per_day, safety, batches = stock[cid]
        d = np.full(180, float(per_day))
        bl = [{"id": hash((cid, i)) % 10**6, "quantity": q, "expiry_date": (TODAY + timedelta(days=e)).isoformat()} for i, (q, e) in enumerate(batches)]
        rows[(cid, "act")] = assess_pair(clinic=c, medicine_id="act", batches=bl, safety=safety, today=TODAY,
                                         demand_central=d, demand_high=d * 1.3, recent_avg_weekly=per_day * 7)
        rows[(cid, "act")]["action"] = ""
        demand[(cid, "act")] = {"central": d, "high": d * 1.3}
    return rows, demand


class RedistributionTests(unittest.TestCase):
    def setUp(self):
        self.clinics = {"A": clinic("A", 20.0, 84.0), "B": clinic("B", 20.1, 84.1), "C": clinic("C", 20.05, 84.05)}

    def run_case(self, stock, clinics=None):
        clinics = clinics or self.clinics
        rows, demand = make(clinics, stock)
        return rows, propose_transfers(clinics, rows, demand, TODAY)

    def test_surplus_clinic_helps_short_clinic(self):
        rows, res = self.run_case({"A": (5, 20, [(3000, 600)]), "B": (10, 20, [(60, 600)]), "C": (5, 20, [(3000, 600)])})
        self.assertTrue(res["proposals"], "expected at least one transfer")
        p = res["proposals"][0]
        self.assertEqual(p["receiver_id"], "B")
        self.assertIn(p["donor_id"], {"A", "C"})
        self.assertGreater(p["receiver_days_after"], p["receiver_days_before"])
        self.assertTrue(p["requires_approval"])
        self.assertEqual(p["status"], "pending")

    def test_donor_safety_stock_and_own_demand_are_protected(self):
        rows, res = self.run_case({"A": (5, 400, [(900, 600)]), "B": (10, 20, [(60, 600)]), "C": (50, 20, [(200, 600)])})
        for p in res["proposals"]:
            donor = rows[(p["donor_id"], "act")]
            moved = sum(b["quantity"] for b in p["batches"])
            reserve = 5 * 1.3 * (donor["lead_time_days"] + 28) + donor["safety_stock"]
            self.assertGreaterEqual(donor["usable_stock"] - moved, reserve - 1)

    def test_expired_and_short_dated_batches_never_move(self):
        stock = {"A": (5, 20, [(2000, -5), (2000, MIN_SHELF_DAYS_AT_ARRIVAL - 20), (50, 600)]),
                 "B": (10, 20, [(60, 600)]), "C": (500, 20, [(10, 600)])}
        rows, res = self.run_case(stock)
        for p in res["proposals"]:
            for b in p["batches"]:
                self.assertGreaterEqual(b["shelf_days_at_arrival"], MIN_SHELF_DAYS_AT_ARRIVAL)
        moved_from_a = sum(b["quantity"] for p in res["proposals"] if p["donor_id"] == "A" for b in p["batches"])
        self.assertLessEqual(moved_from_a, 50)

    def test_clinic_that_is_itself_short_is_not_a_donor(self):
        rows, res = self.run_case({"A": (10, 20, [(100, 600)]), "B": (10, 20, [(60, 600)]), "C": (10, 20, [(80, 600)])})
        self.assertEqual(res["proposals"], [])

    def test_too_far_to_deliver_is_rejected(self):
        far = {"A": clinic("A", 20.0, 84.0), "B": clinic("B", 22.5, 86.5)}      # several hundred km apart
        rows, res = self.run_case({"A": (5, 20, [(5000, 600)]), "B": (10, 20, [(60, 600)])}, far)
        self.assertEqual(res["proposals"], [])

    def test_transfer_must_arrive_before_receiver_runs_out(self):
        near_dead = {"A": (5, 20, [(5000, 600)]), "B": (100, 20, [(60, 600)]), "C": (5, 20, [(10, 600)])}
        rows, res = self.run_case(near_dead)     # B has ~0.6 days of stock; delivery needs >= 1 day
        self.assertTrue(all(p["receiver_id"] != "B" for p in res["proposals"]))

    def test_quantity_never_exceeds_receiver_need(self):
        rows, res = self.run_case({"A": (5, 20, [(9000, 600)]), "B": (10, 20, [(60, 600)]), "C": (5, 20, [(9000, 600)])})
        need = rows[("B", "act")]["reorder_qty"]
        self.assertLessEqual(sum(p["quantity"] for p in res["proposals"] if p["receiver_id"] == "B"), need)


if __name__ == "__main__":
    unittest.main()
