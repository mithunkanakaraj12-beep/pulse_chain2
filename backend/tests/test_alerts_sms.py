import unittest
from unittest import mock

from pulsechain import alerts, db
from pulsechain.config import Settings
from tests.fixtures import make_engine, make_settings


class AlertTextTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = make_engine("alerts")
        cls.engine.set_scenario({"monsoon": True})
        cls.snap = cls.engine.snapshot()

    def test_sms_text_is_short_and_actionable(self):
        eligible = [a for a in self.snap.alerts if a["sms_body"]]
        self.assertTrue(eligible)
        for a in eligible:
            self.assertLessEqual(len(a["sms_body"]), alerts.SMS_MAX_CHARS)
            self.assertIn(a["clinic_name"].split()[0], a["sms_body"])
        stock = next(a for a in eligible if a["type"] == "stock")
        self.assertIn("days", stock["sms_body"])
        self.assertTrue(stock["sms_body"].startswith("ALERT:"))
        self.assertIn("Review stock", stock["sms_body"])

    def test_threshold_controls_which_alerts_get_sms(self):
        for a in self.snap.alerts:
            if a["type"] == "stock":
                self.assertEqual(a["sms_eligible"], a["severity"] in {"high", "critical"})
        strict = make_engine("alerts_strict", alert_risk_threshold="critical")
        strict.set_scenario({"monsoon": True})
        n_strict = sum(a["sms_eligible"] for a in strict.snapshot().alerts if a["type"] == "stock")
        n_default = sum(a["sms_eligible"] for a in self.snap.alerts if a["type"] == "stock")
        self.assertLess(n_strict, n_default)

    def test_phone_numbers_are_reserved_fictional_numbers(self):
        for a in self.snap.alerts:
            self.assertTrue(a["to_number"].startswith("+1202555"), a["to_number"])


class SmsSafetyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = make_engine("sms")
        cls.engine.set_scenario({"monsoon": True})
        cls.alert = next(a for a in cls.engine.snapshot().alerts if a["sms_body"])

    def send(self, settings, confirm=False):
        from pulsechain import sms
        with db.session(settings.db_path) as conn:
            return sms.send_alert_sms(settings, conn, self.alert, confirm_live=confirm)

    def test_simulated_mode_never_contacts_twilio(self):
        with mock.patch("pulsechain.sms.requests.post", side_effect=AssertionError("network used")):
            res = self.send(make_settings("sms"))
        self.assertEqual((res["mode"], res["status"]), ("simulated", "simulated"))

    def test_live_mode_without_safeguards_is_blocked(self):
        live_no_creds = Settings(**{**make_settings("sms").__dict__, "sms_mode": "live"})
        with mock.patch("pulsechain.sms.requests.post", side_effect=AssertionError("network used")):
            self.assertEqual(self.send(live_no_creds, confirm=True)["status"], "blocked")

    def test_live_mode_needs_explicit_confirmation_each_time(self):
        live = Settings(**{**make_settings("sms").__dict__, "sms_mode": "live", "sms_allow_live": True,
                           "twilio_account_sid": "ACtest", "twilio_auth_token": "tok", "twilio_from_number": "+15005550006"})
        with mock.patch("pulsechain.sms.requests.post", side_effect=AssertionError("network used")):
            res = self.send(live, confirm=False)
        self.assertEqual(res["status"], "blocked")
        self.assertIn("confirm", res["error"].lower())

    def test_live_send_works_only_with_every_safeguard(self):
        live = Settings(**{**make_settings("sms").__dict__, "sms_mode": "live", "sms_allow_live": True,
                           "twilio_account_sid": "ACtest", "twilio_auth_token": "tok", "twilio_from_number": "+15005550006"})
        fake = mock.Mock(status_code=201)
        fake.json.return_value = {"sid": "SM123"}
        with mock.patch("pulsechain.sms.requests.post", return_value=fake) as post:
            res = self.send(live, confirm=True)
        self.assertEqual(res["status"], "sent")
        self.assertEqual(res["provider_sid"], "SM123")
        self.assertEqual(post.call_args.kwargs["data"]["To"], self.alert["to_number"])

    def test_alert_without_sms_text_cannot_be_sent(self):
        from pulsechain import sms
        low = {"key": "x", "clinic_id": "C01", "to_number": "+12025550101", "sms_body": None}
        with db.session(make_settings("sms").db_path) as conn:
            with self.assertRaises(ValueError):
                sms.send_alert_sms(make_settings("sms"), conn, low)


if __name__ == "__main__":
    unittest.main()
