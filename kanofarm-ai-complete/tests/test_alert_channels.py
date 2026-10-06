"""Alerts outside the app: pure logic only (no network, no database). Channels and storage are replaced with fakes."""
import json, unittest
from datetime import date
from kanofarm.services import notify
from kanofarm.services.alerts_job import run_alert_job
from kanofarm.services.i18n import translate
from kanofarm.services.validation import alert_prefs_payload, push_subscription_payload, ValidationError
from kanofarm.services.weather_parser import parse_forecast, WeatherDataError
from tests.test_weather import payload

TODAY = date(2026, 9, 24)
FC = parse_forecast(payload([0] * 7, [60, 0, 0, 0, 0, 0, 0]))     # heavy rain (warning) + recent dry spell (watch) + rain delay (info)


class Helpers(unittest.TestCase):
    def test_levels(self):
        self.assertTrue(notify.level_allowed("warning", "watch"))
        self.assertTrue(notify.level_allowed("watch", "watch"))
        self.assertFalse(notify.level_allowed("info", "watch"))
        self.assertTrue(notify.level_allowed("info", "info"))
        self.assertFalse(notify.level_allowed("watch", "warning"))
        self.assertTrue(notify.level_allowed("info", None) is False)       # default is "watch"

    def test_quiet_hours(self):
        self.assertFalse(notify.in_quiet_hours(None, None, 6))
        self.assertTrue(notify.in_quiet_hours(22, 7, 23))
        self.assertTrue(notify.in_quiet_hours(22, 7, 3))
        self.assertFalse(notify.in_quiet_hours(22, 7, 7))                   # end is exclusive
        self.assertFalse(notify.in_quiet_hours(22, 7, 12))
        self.assertTrue(notify.in_quiet_hours(1, 5, 1))
        self.assertFalse(notify.in_quiet_hours(1, 5, 5))
        self.assertFalse(notify.in_quiet_hours(8, 8, 8))                    # equal means never quiet

    def test_wanted_channels_need_site_support_and_phone(self):
        t = {"push_enabled": True, "whatsapp_enabled": True, "sms_enabled": True, "phone": "+2348031234567"}
        self.assertEqual(notify.wanted_channels(t, {"push": True, "whatsapp": False, "sms": False}), ["push"])
        self.assertEqual(notify.wanted_channels(t, {"push": True, "whatsapp": True, "sms": False}), ["push", "whatsapp"])
        self.assertEqual(notify.wanted_channels({**t, "phone": None}, {"push": False, "whatsapp": True, "sms": True}), [])
        self.assertEqual(notify.wanted_channels({"push_enabled": False}, {"push": True}), [])

    def test_message_is_trimmed(self):
        m = notify.build_message("F" * 200, "x" * 500)
        self.assertLessEqual(len(m["title"]), 80); self.assertLessEqual(len(m["body"]), 240)


class Channels(unittest.TestCase):
    def test_push_success_and_gone(self):
        sent = []
        ok = notify.WebPushChannel("priv", "mailto:a@b.c", sender=lambda sub, data, key, claims: sent.append((sub, data, claims)))
        r = ok.send({"endpoint": "https://e", "p256dh": "p", "auth": "a"}, {"title": "t", "body": "b"})
        self.assertTrue(r.ok); self.assertEqual(json.loads(sent[0][1])["title"], "t")

        class Resp: status_code = 410
        class Boom(Exception): response = Resp()
        def raiser(*a): raise Boom()
        r = notify.WebPushChannel("priv", "mailto:a@b.c", sender=raiser).send({"endpoint": "e", "p256dh": "p", "auth": "a"}, {})
        self.assertFalse(r.ok); self.assertTrue(r.gone)

    def test_push_other_error_is_not_gone(self):
        def raiser(*a): raise RuntimeError("network")
        r = notify.WebPushChannel("k", "s", sender=raiser).send({"endpoint": "e", "p256dh": "p", "auth": "a"}, {})
        self.assertFalse(r.ok); self.assertFalse(r.gone)

    def test_unconfigured(self):
        self.assertFalse(notify.WebPushChannel("", "").configured)
        self.assertFalse(notify.WhatsAppCloudChannel("", "", "").configured)
        self.assertFalse(notify.SmsChannel().configured)
        self.assertFalse(notify.SmsChannel().send("+2348031234567", {}).ok)

    def test_whatsapp_payload_uses_template_and_strips_plus(self):
        calls = []
        ch = notify.WhatsAppCloudChannel("tok", "123", "farm_alert", "en", post=lambda u, b, t: calls.append((u, b, t)))
        self.assertTrue(ch.configured)
        self.assertTrue(ch.send("+2348031234567", {"body": "Heavy rain"}, "My farm").ok)
        url, body, tok = calls[0]
        self.assertIn("/123/messages", url); self.assertEqual(body["to"], "2348031234567")
        self.assertEqual(body["type"], "template"); self.assertEqual(body["template"]["name"], "farm_alert")
        self.assertEqual(tok, "tok")

    def test_whatsapp_failure_is_reported_not_raised(self):
        def bad(*a): raise RuntimeError("x")
        self.assertFalse(notify.WhatsAppCloudChannel("t", "1", "n", post=bad).send("+2348031234567", {"body": "b"}).ok)


class FakeChannel:
    def __init__(self, name, result=None, per_sub=None):
        self.name, self.calls, self.result, self.per_sub = name, [], result or notify.SendResult(True), per_sub
    def send(self, dest, msg, farm_name=""):
        self.calls.append((dest, msg))
        if self.per_sub: return self.per_sub(dest)
        return self.result


class Job(unittest.TestCase):
    def setUp(self):
        self.claims, self.released, self.dropped = set(), [], []
        self.push = FakeChannel("push")

    def run_job(self, targets, subs=None, hour=6, push=None, available=None, forecast=None):
        return run_alert_job(
            targets=targets, subs_by_user=subs if subs is not None else {"u1": [{"endpoint": "https://e1", "p256dh": "p", "auth": "a"}]},
            get_forecast=forecast or (lambda la, lo: FC), translate=translate,
            channels={"push": push or self.push, "whatsapp": FakeChannel("whatsapp"), "sms": FakeChannel("sms")},
            available=available or {"push": True, "whatsapp": False, "sms": False},
            claim=lambda f, k, c: (f, k, c) not in self.claims and not self.claims.add((f, k, c)),
            release=lambda f, k, c: (self.released.append((f, k, c)), self.claims.discard((f, k, c))),
            drop_sub=self.dropped.append, today=TODAY, lagos_hour=hour)

    def target(self, **kw):
        t = {"user_id": "u1", "farm_id": "f1", "farm_name": "Farm", "latitude": 12.0, "longitude": 8.5, "language": "en",
             "push_enabled": True, "whatsapp_enabled": False, "sms_enabled": False, "phone": None, "min_level": "watch",
             "quiet_start": None, "quiet_end": None, "rules": []}
        t.update(kw); return t

    def test_sends_watch_and_warning_only_by_default(self):
        s = self.run_job([self.target()])
        self.assertEqual(s["sent"], 2)                               # heavy rain (warning) + recent dry spell (watch); info skipped
        bodies = " ".join(m["body"] for _, m in self.push.calls)
        self.assertIn("Heavy rainfall", bodies); self.assertNotIn("delaying irrigation", bodies)

    def test_everything_when_farmer_asks(self):
        self.assertEqual(self.run_job([self.target(min_level="info")])["sent"], 3)

    def test_warnings_only(self):
        self.assertEqual(self.run_job([self.target(min_level="warning")])["sent"], 1)

    def test_second_run_same_day_sends_nothing(self):
        self.run_job([self.target()]); n = len(self.push.calls)
        s = self.run_job([self.target()])
        self.assertEqual(s["sent"], 0); self.assertEqual(s["duplicates"], 2); self.assertEqual(len(self.push.calls), n)

    def test_failed_send_releases_claim_so_it_can_retry(self):
        bad = FakeChannel("push", result=notify.SendResult(False, error="boom"))
        s = self.run_job([self.target()], push=bad)
        self.assertEqual(s["failed"], 2); self.assertEqual(len(self.released), 2); self.assertEqual(self.claims, set())
        self.assertEqual(self.run_job([self.target()])["sent"], 2)  # next run succeeds

    def test_gone_subscription_is_dropped(self):
        ch = FakeChannel("push", per_sub=lambda sub: notify.SendResult(False, gone=sub["endpoint"] == "https://dead"))
        subs = {"u1": [{"endpoint": "https://dead"}, {"endpoint": "https://live"}]}
        # "live" also fails (gone False) so nothing is delivered; only the dead one is forgotten
        s = self.run_job([self.target(min_level="warning")], subs=subs, push=ch)
        self.assertEqual(self.dropped, ["https://dead"]); self.assertEqual(s["failed"], 1)

    def test_one_good_device_is_enough(self):
        ch = FakeChannel("push", per_sub=lambda sub: notify.SendResult(sub["endpoint"] == "https://live"))
        subs = {"u1": [{"endpoint": "https://bad"}, {"endpoint": "https://live"}]}
        self.assertEqual(self.run_job([self.target(min_level="warning")], subs=subs, push=ch)["sent"], 1)

    def test_quiet_hours_skip_without_claiming(self):
        s = self.run_job([self.target(quiet_start=4, quiet_end=8)], hour=6)
        self.assertEqual(s["skipped_quiet"], 1); self.assertEqual(s["sent"], 0); self.assertEqual(self.claims, set())

    def test_weather_failure_is_counted_and_does_not_crash(self):
        def boom(la, lo): raise WeatherDataError("down")
        s = self.run_job([self.target(), self.target(farm_id="f2")], forecast=boom)
        self.assertEqual(s["weather_errors"], 2); self.assertEqual(s["sent"], 0)

    def test_farmer_custom_rules_and_switch_off_are_respected(self):
        rules = [{"alert_type": "heavy_rain", "enabled": False}]
        self.assertEqual(self.run_job([self.target(min_level="warning", rules=rules)])["sent"], 0)

    def test_farm_with_no_usable_channel_is_ignored(self):
        s = self.run_job([self.target()], available={"push": False, "whatsapp": False, "sms": False})
        self.assertEqual(s["farms"], 0); self.assertEqual(self.push.calls, [])

    def test_phone_channel_sends_to_the_stored_number(self):
        wa = FakeChannel("whatsapp")
        t = self.target(push_enabled=False, whatsapp_enabled=True, phone="+2348031234567", min_level="warning")
        s = run_alert_job(targets=[t], subs_by_user={}, get_forecast=lambda la, lo: FC, translate=translate,
                          channels={"push": self.push, "whatsapp": wa, "sms": FakeChannel("sms")},
                          available={"push": False, "whatsapp": True, "sms": False}, claim=lambda *a: True,
                          release=lambda *a: None, drop_sub=lambda e: None, today=TODAY, lagos_hour=6)
        self.assertEqual(s["sent"], 1); self.assertEqual(wa.calls[0][0], "+2348031234567")

    def test_farmers_language_is_used(self):
        self.run_job([self.target(language="ha", min_level="warning")])
        self.assertIn("ruwan sama", self.push.calls[0][1]["body"])


class Validation(unittest.TestCase):
    def test_defaults_are_everything_off(self):
        p = alert_prefs_payload({})
        self.assertFalse(p["push_enabled"] or p["whatsapp_enabled"] or p["sms_enabled"]); self.assertEqual(p["min_level"], "watch")
        self.assertIsNone(p["phone"])

    def test_phone_channels_need_number_and_consent(self):
        with self.assertRaises(ValidationError): alert_prefs_payload({"whatsapp_enabled": True})
        with self.assertRaises(ValidationError): alert_prefs_payload({"sms_enabled": True, "phone": "0803 123 4567"})
        p = alert_prefs_payload({"whatsapp_enabled": True, "phone": "0803 123 4567", "phone_consent": True})
        self.assertEqual(p["phone"], "+2348031234567")

    def test_number_is_dropped_without_consent(self):
        self.assertIsNone(alert_prefs_payload({"phone": "0803 123 4567", "phone_consent": False})["phone"])

    def test_bad_inputs(self):
        for bad in ({"phone": "12345"}, {"min_level": "all"}, {"quiet_start": 25, "quiet_end": 3},
                    {"quiet_start": 3}, {"quiet_start": "x", "quiet_end": 3}):
            with self.assertRaises(ValidationError, msg=str(bad)): alert_prefs_payload(bad)

    def test_truthy_strings_do_not_enable_anything(self):
        self.assertFalse(alert_prefs_payload({"push_enabled": "true"})["push_enabled"])

    def test_quiet_hours_pair(self):
        p = alert_prefs_payload({"quiet_start": "22", "quiet_end": "6"})
        self.assertEqual((p["quiet_start"], p["quiet_end"]), (22, 6))
        self.assertEqual(alert_prefs_payload({"quiet_start": "", "quiet_end": ""})["quiet_start"], None)

    def test_push_subscription(self):
        ok = {"endpoint": "https://fcm.googleapis.com/fcm/send/abcdef123456", "keys": {"p256dh": "x" * 30, "auth": "y" * 12}}
        self.assertEqual(push_subscription_payload(ok)["auth"], "y" * 12)
        for bad in ({}, {**ok, "endpoint": "http://insecure.example/abcdefghij"}, {**ok, "keys": {}},
                    {**ok, "keys": {"p256dh": "short", "auth": "y" * 12}}, {**ok, "endpoint": "https://" + "a" * 1000}):
            with self.assertRaises(ValidationError): push_subscription_payload(bad)


class Vapid(unittest.TestCase):
    def test_generated_keys_have_the_expected_shape(self):
        import base64
        from scripts.generate_vapid import generate
        k = generate()
        pad = lambda s: s + "=" * (-len(s) % 4)
        pub = base64.urlsafe_b64decode(pad(k["VAPID_PUBLIC_KEY"]))
        self.assertEqual(len(pub), 65); self.assertEqual(pub[0], 4)       # uncompressed P-256 point
        self.assertNotEqual(k["VAPID_PRIVATE_KEY"], k["VAPID_PUBLIC_KEY"])


if __name__ == "__main__":
    unittest.main()
