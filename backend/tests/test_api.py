import unittest
from datetime import date

from pulsechain import db
from pulsechain.api import create_app
from tests.fixtures import make_engine, make_settings


class ApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.settings = make_settings("api")
        make_engine("api")
        cls.client = create_app(cls.settings).test_client()

    def setUp(self):
        self.client.post("/api/admin/reset")

    def get(self, path):
        r = self.client.get(path)
        self.assertEqual(r.status_code, 200, f"{path}: {r.data[:200]}")
        return r.get_json()

    def test_every_documented_route_returns_200(self):
        for path in ["/api/health", "/api/meta", "/api/model", "/api/summary", "/api/clinics", "/api/clinics/C01",
                     "/api/forecast", "/api/forecast?disease=cholera&region=coastal&weeks=8", "/api/scenario",
                     "/api/demo/walkthrough", "/api/inventory", "/api/inventory?clinic_id=C04&risk=critical",
                     "/api/transfers", "/api/alerts", "/api/sms/log", "/api/weather/live?clinic_id=C02", "/"]:
            if path == "/":
                resp = self.client.get("/")
                self.assertEqual(resp.status_code, 200)
                resp.close()
            else:
                self.get(path)

    def test_bad_input_gets_clear_client_errors(self):
        self.assertEqual(self.client.get("/api/clinics/NOPE").status_code, 404)
        self.assertEqual(self.client.get("/api/forecast?disease=flu").status_code, 400)
        self.assertEqual(self.client.get("/api/forecast?weeks=99").status_code, 400)
        self.assertEqual(self.client.get("/api/forecast?weeks=abc").status_code, 400)
        self.assertEqual(self.client.get("/api/inventory?risk=scary").status_code, 400)
        self.assertEqual(self.client.get("/api/nothing-here").status_code, 404)
        self.assertEqual(self.client.post("/api/scenario", json={"intensity": 50}).status_code, 400)
        self.assertEqual(self.client.post("/api/scenario", json={"monsoon": "yes"}).status_code, 400)
        self.assertEqual(self.client.post("/api/scenario", json={"regions": ["mars"]}).status_code, 400)
        self.assertEqual(self.client.patch("/api/inventory/batches/1", json={"quantity": -5}).status_code, 400)
        self.assertEqual(self.client.patch("/api/inventory/batches/1", json={"quantity": 1.5}).status_code, 400)
        self.assertEqual(self.client.patch("/api/inventory/batches/999999", json={"quantity": 5}).status_code, 404)
        self.assertEqual(self.client.post("/api/inventory/batches", json={"clinic_id": "C01"}).status_code, 400)
        self.assertEqual(self.client.post("/api/inventory/batches", json={"clinic_id": "C01", "medicine_id": "act", "quantity": 5, "expiry_date": "soon"}).status_code, 400)
        self.assertEqual(self.client.post("/api/transfers/decision", json={"key": "nope", "decision": "approve"}).status_code, 404)
        self.assertEqual(self.client.post("/api/transfers/decision", json={"key": "x", "decision": "maybe"}).status_code, 400)
        self.assertEqual(self.client.post("/api/sms/send", json={}).status_code, 400)

    def test_editing_inventory_updates_downstream_calculations(self):
        row = next(r for r in self.get("/api/inventory?clinic_id=C04&medicine_id=act")["rows"])
        batch = next(b for b in row["batches"] if not b["expired"])
        self.client.patch(f"/api/inventory/batches/{batch['id']}", json={"quantity": batch["quantity"] + 5000})
        after = next(r for r in self.get("/api/inventory?clinic_id=C04&medicine_id=act")["rows"])
        self.assertGreater(after["days_to_stockout"] or 999, row["days_to_stockout"])
        self.assertEqual(after["risk"], "low")
        added = self.client.post("/api/inventory/batches", json={"clinic_id": "C04", "medicine_id": "rdt", "quantity": 100, "expiry_date": "2027-12-31"})
        self.assertEqual(added.status_code, 201)

    def test_scenario_changes_summary(self):
        calm = self.get("/api/summary")["kpis"]
        self.client.post("/api/scenario", json={"monsoon": True})
        wet = self.get("/api/summary")["kpis"]
        self.assertGreater(wet["pairs_at_risk"], calm["pairs_at_risk"])
        self.assertGreater(wet["redistribution_opportunities"], calm["redistribution_opportunities"])

    def test_transfer_approval_moves_stock_and_needs_a_decision(self):
        self.client.post("/api/scenario", json={"monsoon": True})
        before = self.get("/api/transfers")
        self.assertTrue(before["approval_required"])
        p = before["proposals"][0]
        inv_before = {(r["clinic_id"], r["medicine_id"]): r for r in self.get("/api/inventory")["rows"]}
        self.assertEqual(p["status"], "pending")
        res = self.client.post("/api/transfers/decision", json={"key": p["key"], "decision": "approve"})
        self.assertEqual(res.status_code, 200)
        inv_after = {(r["clinic_id"], r["medicine_id"]): r for r in self.get("/api/inventory")["rows"]}
        d, r = (p["donor_id"], p["medicine_id"]), (p["receiver_id"], p["medicine_id"])
        self.assertEqual(inv_before[d]["usable_stock"] - inv_after[d]["usable_stock"], p["quantity"])
        self.assertEqual(inv_after[r]["usable_stock"] - inv_before[r]["usable_stock"], p["quantity"])
        self.assertGreaterEqual(inv_after[d]["usable_stock"], inv_after[d]["safety_stock"])
        self.assertEqual(len(self.get("/api/transfers")["history"]), 1)

    def test_rejecting_hides_the_proposal_without_moving_stock(self):
        self.client.post("/api/scenario", json={"monsoon": True})
        p = self.get("/api/transfers")["proposals"][0]
        total_before = self.get("/api/summary")["kpis"]["total_inventory_units"]
        self.client.post("/api/transfers/decision", json={"key": p["key"], "decision": "reject"})
        self.assertNotIn(p["key"], [x["key"] for x in self.get("/api/transfers")["proposals"]])
        self.assertEqual(self.get("/api/summary")["kpis"]["total_inventory_units"], total_before)

    def test_sms_send_is_simulated_and_logged_and_ack_works(self):
        self.client.post("/api/scenario", json={"monsoon": True})
        alert = next(a for a in self.get("/api/alerts")["alerts"] if a["sms_eligible"])
        res = self.client.post("/api/sms/send", json={"alert_key": alert["key"]})
        self.assertEqual((res.status_code, res.get_json()["status"]), (200, "simulated"))
        self.assertEqual(len(self.get("/api/sms/log")["log"]), 1)
        low = next(a for a in self.get("/api/alerts")["alerts"] if not a["sms_eligible"])
        self.assertEqual(self.client.post("/api/sms/send", json={"alert_key": low["key"]}).status_code, 400)
        self.assertEqual(self.client.post("/api/alerts/ack", json={"key": alert["key"]}).status_code, 200)
        acked = next(a for a in self.get("/api/alerts")["alerts"] if a["key"] == alert["key"])
        self.assertTrue(acked["acknowledged"])

    def test_live_weather_falls_back_when_disabled(self):
        w = self.get("/api/weather/live?clinic_id=C01")
        self.assertFalse(w["available"])
        self.assertIn("synthetic", w["reason"])

    def test_reset_restores_original_data(self):
        self.client.post("/api/scenario", json={"monsoon": True})
        self.client.post("/api/admin/reset")
        self.assertFalse(self.get("/api/scenario")["monsoon"])

    def test_everything_is_labelled_simulated(self):
        self.assertIn("SIMULATED", self.get("/api/meta")["data_label"])
        self.assertIn("SIMULATED", self.get("/api/model")["data_label"])
        self.assertIn("SIMULATED", self.get("/api/summary")["data_label"])


if __name__ == "__main__":
    unittest.main()
