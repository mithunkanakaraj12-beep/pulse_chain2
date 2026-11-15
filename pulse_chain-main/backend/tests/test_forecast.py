import unittest

import numpy as np

from pulsechain import data_gen, db, forecast as fc
from pulsechain.history import load_history
from tests.fixtures import make_engine, model_dir, make_settings


class DataTests(unittest.TestCase):
    def test_generator_is_deterministic_and_complete(self):
        s = make_settings("data")
        with db.session(s.db_path) as conn:
            db.drop_all(conn); db.init_schema(conn)
            a = data_gen.generate(conn)
        with db.session(s.db_path) as conn:
            db.drop_all(conn); db.init_schema(conn)
            b = data_gen.generate(conn)
        for k in ("clinics", "weather_rows", "case_rows", "consumption_rows", "batches"):
            self.assertEqual(a[k], b[k])
        self.assertEqual((a["clinics"], a["weather_rows"], a["batches"]), (12, 3120, 148))

    def test_dataset_meets_brief(self):
        eng = make_engine("brief")
        with db.session(eng.s.db_path) as conn:
            self.assertGreaterEqual(db.one(conn, "SELECT COUNT(*) n FROM clinics")["n"], 10)
            self.assertGreaterEqual(db.one(conn, "SELECT COUNT(DISTINCT disease) n FROM cases")["n"], 3)
            self.assertGreaterEqual(db.one(conn, "SELECT COUNT(DISTINCT medicine_id) n FROM consumption")["n"], 5)
            self.assertGreater(db.one(conn, "SELECT COUNT(*) n FROM inventory_batches WHERE expiry_date < '2026-05-25'")["n"], 0)


class ModelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = make_engine("forecast")
        cls.metrics = fc.load_metrics(model_dir())
        with db.session(cls.engine.s.db_path) as conn:
            cls.hist = load_history(conn)
        cls.f = fc.Forecaster(model_dir())

    def test_model_beats_both_baselines_on_held_out_year(self):
        for disease, m in self.metrics["metrics"]["diseases"].items():
            self.assertLess(m["mae_model"], m["mae_seasonal_naive"], disease)
            self.assertLess(m["mae_model"], m["mae_persistence"], disease)

    def test_prediction_intervals_are_reasonably_calibrated(self):
        for disease, m in self.metrics["metrics"]["diseases"].items():
            self.assertGreater(m["interval_coverage_80"], 0.70, disease)

    def test_forecast_shape_and_ordering(self):
        out = self.f.forecast(self.hist, None)
        self.assertEqual(len(out), 12)
        for cid, per in out.items():
            for disease, v in per.items():
                self.assertEqual(len(v["median"]), 8)
                self.assertTrue(all(lo <= m <= hi for lo, m, hi in zip(v["p10"], v["median"], v["p90"])), (cid, disease))
                self.assertTrue(all(x >= 0 for x in v["p10"]))

    def test_monsoon_raises_forecast_for_rain_driven_diseases_only_modestly_for_pneumonia(self):
        base = self.f.forecast(self.hist, None)
        mon = self.f.forecast(self.hist, {**fc.default_scenario(), "monsoon": True})
        tot = lambda f, d: sum(sum(f[c][d]["median"]) for c in f)
        self.assertGreater(tot(mon, "malaria"), tot(base, "malaria") * 1.15)
        self.assertGreater(tot(mon, "cholera"), tot(base, "cholera") * 2.0)
        self.assertLess(tot(mon, "pneumonia"), tot(base, "pneumonia") * 1.3)

    def test_monsoon_scenario_respects_region_selection(self):
        sc = {**fc.default_scenario(), "monsoon": True, "regions": ["coastal"]}
        base = self.f.forecast(self.hist, None)
        mon = self.f.forecast(self.hist, sc)
        for cid, c in self.hist.clinics.items():
            changed = mon[cid]["cholera"]["median"] != base[cid]["cholera"]["median"]
            self.assertEqual(changed, c["region"] == "coastal", cid)

    def test_forecast_feeds_inventory_recommendations(self):
        self.engine.set_scenario({"monsoon": False})
        calm = self.engine.snapshot()
        self.engine.set_scenario({"monsoon": True})
        wet = self.engine.snapshot()
        at_risk = lambda s: sum(r["risk"] != "low" for r in s.rows.values())
        self.assertGreater(at_risk(wet), at_risk(calm))
        c = calm.rows[("C04", "act")]
        w = wet.rows[("C04", "act")]
        self.assertLess(w["days_to_stockout"], c["days_to_stockout"])
        self.assertGreaterEqual(w["reorder_qty"], c["reorder_qty"])
        self.engine.set_scenario({"monsoon": False})


if __name__ == "__main__":
    unittest.main()
