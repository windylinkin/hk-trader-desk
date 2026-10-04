import datetime as dt
import tempfile
import unittest
import sys
import base64
from pathlib import Path
from unittest.mock import patch, Mock
from fastapi.testclient import TestClient
from pattern_engine import Pattern, PatternEngine
from workstation_store import Store
from mobile_push import MobilePush, MobileSettings, crypt
from market_analysis import indicators
import workstation_app as app
import pandas as pd


class RulesTest(unittest.TestCase):
    def rule(self, **kw):
        return Pattern(
            **dict(
                dict(
                    id="test",
                    name="test",
                    kind="drop",
                    threshold=15,
                    window_sec=60,
                    warmup_sec=0,
                    min_samples=2,
                    cooldown_sec=60,
                ),
                **kw,
            )
        )

    def row(self, p, v=100):
        return dict(code="HK.00700", name="腾讯", last_price=p, volume=v, turnover=10000, suspension=False)

    def test_drop_and_cooldown(self):
        e = PatternEngine()
        r = [self.rule()]
        self.assertFalse(e.evaluate(self.row(100), 0, "2026-10-01 10:00:00", r, set()))
        self.assertEqual(len(e.evaluate(self.row(80), 5, "2026-10-01 10:00:05", r, set())), 1)
        self.assertFalse(e.evaluate(self.row(79), 10, "2026-10-01 10:00:10", r, set()))

    def test_volume_reset_and_scope(self):
        e = PatternEngine()
        r = [self.rule(kind="volume", threshold=100, scope="watchlist")]
        self.assertFalse(e.evaluate(self.row(100), 0, "", r, set()))
        e.evaluate(self.row(100, 1000), 1, "", r, {"HK.00700"})
        self.assertEqual(len(e.evaluate(self.row(100, 1100), 2, "", r, {"HK.00700"})), 1)
        self.assertFalse(e.evaluate(self.row(100, 10), 3, "", r, {"HK.00700"}))

    def test_breakout_excludes_current(self):
        e = PatternEngine()
        r = [self.rule(kind="breakout", threshold=1)]
        e.evaluate(self.row(100), 1, "", r, set())
        self.assertEqual(len(e.evaluate(self.row(102), 2, "", r, set())), 1)

    def test_rule_change_resets_warmup(self):
        e = PatternEngine()
        e.evaluate(self.row(100), 1, "", [self.rule()], set())
        self.assertFalse(e.evaluate(self.row(80), 2, "", [self.rule(threshold=10)], set()))

    def test_future_indicator_independence(self):
        bars = [dict(time=i, open=100 + i, high=102 + i, low=99 + i, close=101 + i, volume=100 + i) for i in range(100)]
        all_data = indicators(bars)["series"]
        part = indicators(bars[:50])["series"]
        for k in part:
            self.assertEqual(part[k], [x for x in all_data[k] if x["time"] < 50])

    def test_worker_end_to_end_without_external_push(self):
        class FakeStop:
            stopped = False

            def is_set(self):
                return self.stopped

            def set(self):
                self.stopped = True

            def wait(self, t):
                pass

        class FakeContext:
            calls = 0
            closed = False

            def get_stock_basicinfo(self, *args):
                return 0, pd.DataFrame(
                    [dict(code="HK.00700", name="腾讯", exchange_type="HK_MAINBOARD", delisting=False)]
                )

            def request_trading_days(self, *args, **kw):
                return 0, [dict(trade_date_type="WHOLE")]

            def get_market_snapshot(self, codes):
                self.calls += 1
                if self.calls > 3:
                    raise AssertionError("worker failed to trigger")
                return 0, pd.DataFrame(
                    [
                        dict(
                            code="HK.00700",
                            last_price=100 if self.calls == 1 else 80,
                            volume=100,
                            turnover=10000,
                            prev_close_price=100,
                            suspension=False,
                        )
                    ]
                )

            def close(self):
                self.closed = True

        event = FakeStop()
        ctx = FakeContext()
        sock = Mock()
        sock.__enter__ = Mock()
        sock.__exit__ = Mock()
        with (
            patch.object(app, "stop", event),
            patch.object(app, "patterns", [self.rule()]),
            patch.object(app, "session", return_value="morning"),
            patch.object(app, "OpenQuoteContext", return_value=ctx),
            patch.object(app.socket, "create_connection", return_value=sock),
            patch.object(app, "publish", side_effect=lambda a: event.set()) as publish,
        ):
            app.monitor_worker()
        self.assertTrue(ctx.closed)
        self.assertEqual(publish.call_count, 1)


class StorageAndPush(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / "test.db")

        def fake_crypt(value, decrypt=False):
            return (
                base64.b64decode(value[5:]).decode() if decrypt else "test:" + base64.b64encode(value.encode()).decode()
            )

        self.crypt_patch = patch("mobile_push.crypt", side_effect=fake_crypt)
        self.crypt_patch.start()

    def tearDown(self):
        self.crypt_patch.stop()
        self.temp.cleanup()

    def test_persistence_and_notes(self):
        a = dict(id="x", timestamp="2026-10-01 10:00:00", code="HK.00700", price=100)
        self.store.add_alert(a)
        self.store.add_alert(a)
        self.assertEqual(self.store.count(), 1)
        self.store.note("x", "有效信号", "test", "now")
        self.assertEqual(self.store.alerts()[0]["review"]["note"], "test")
        self.assertEqual(Store(self.store.path).count(), 1)

    @unittest.skipUnless(sys.platform == "win32", "Windows DPAPI only")
    def test_dpapi(self):
        self.assertEqual(crypt(crypt("secret"), True), "secret")

    def test_mobile_redaction_and_mock_transport(self):
        m = MobilePush(self.store)
        m.save(
            MobileSettings(enabled=True, telegram_enabled=True, telegram_token="123:abc_def", telegram_chat_id="123")
        )
        self.assertNotIn("telegram_token", m.settings())
        self.assertNotIn("123:abc_def", str(self.store.get("mobile")))
        with patch("mobile_push.httpx.post", return_value=Mock(status_code=200, json=lambda: {"ok": True})) as post:
            self.assertTrue(m.send("test", "body")[0]["success"])
            self.assertEqual(post.call_count, 1)
        m.save(MobileSettings(enabled=False))
        self.assertFalse(m.send("x", "y")[0]["success"])

    def test_bark_transport(self):
        m = MobilePush(self.store)
        m.save(MobileSettings(enabled=True, bark_enabled=True, bark_key="test_key"))
        with patch("mobile_push.httpx.post", return_value=Mock(status_code=200, json=lambda: {"code": 200})) as post:
            self.assertTrue(m.send("test", "body")[0]["success"])
            self.assertEqual(post.call_args[1]["json"]["device_key"], "test_key")

    def test_outcome_missing_not_zero(self):
        a = dict(id="x", timestamp="2026-10-01 10:00:00", code="HK.00700", price=100, epoch=1000)
        self.store.add_alert(a)
        with patch.object(app, "store", self.store):
            app.update_outcomes([(a, set(), "morning")], [dict(code="HK.00700", last_price=105)], 1301, "morning")
        self.assertEqual(self.store.alerts()[0]["outcomes"]["300"]["return_pct"], 5.000000000000004)
        self.assertNotIn("900", self.store.alerts()[0]["outcomes"])

    def test_api_validation_and_local_origin(self):
        with patch.object(app, "store", self.store):
            c = TestClient(app.app)
            self.assertEqual(c.get("/").status_code, 200)
            self.assertEqual(c.post("/api/patterns", json={"patterns": []}).status_code, 422)
            self.assertEqual(c.post("/api/scanner", json={"scan_interval": 1, "watchlist": []}).status_code, 422)
            self.assertEqual(
                c.post("/api/scanner", json={}, headers={"Origin": "https://evil.example"}).status_code, 403
            )
            self.assertEqual(c.get("/api/chart?code=bad").status_code, 422)


if __name__ == "__main__":
    unittest.main()
