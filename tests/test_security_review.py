"""Regression tests for WAN health, VPN, webhook events and metadata sanitization."""
import importlib.util
from pathlib import Path
import unittest

path = Path(__file__).resolve().parents[1] / "custom_components" / "unifi_network_monitor" / "security.py"
spec = importlib.util.spec_from_file_location("local_security_review", path)
module = importlib.util.module_from_spec(spec)
assert spec is not None and spec.loader is not None
spec.loader.exec_module(module)


class TestSecurityReview(unittest.TestCase):
    def test_wan_health_allowlist(self):
        data = {"status": "ok", "password": "WAN_SECRET", "uptime_stats": {
            "WAN": {"uptime": 100, "latency_average": 12, "private_key": "SECRET", "nested": {"token": "SECRET"}},
            "WAN2": {"availability": 99.9},
            "UNKNOWN": {"token": "SECRET"}}}
        out = module.safe_wan_health(data)
        self.assertEqual(out["uptime_stats"]["WAN"], {"uptime": 100, "latency_average": 12})
        self.assertNotIn("SECRET", str(out))

    def test_vpn_rows(self):
        out = module.safe_vpn_rows([{"label": "Test VPN", "ip": "192.0.2.1", "password": "SECRET", "nested": {"token": "SECRET"}}])
        self.assertEqual(out, [{"label": "Test VPN", "ip": "192.0.2.1"}])

    def test_webhook_events(self):
        event = {"ts": "2026-11-09", "wan_name": "WAN1", "message": "down", "query": {"token": "SECRET"}, "parameters": {"password": "SECRET"}}
        self.assertEqual(module.safe_wan_event(event)["event"], "WAN Event")
        self.assertNotIn("SECRET", str(module.safe_wan_events([event])))

    def test_meta(self):
        meta = {"ts": 123, "stale": True, "stale_error": "SECRET", "host": "internal.example", "password": "SECRET"}
        self.assertEqual(module.safe_public_meta(meta), {"ts": 123, "stale": True})

    def test_malformed_rows(self):
        self.assertEqual(module.safe_vpn_rows([None, "bad"]), [])
        self.assertEqual(module.safe_wan_health(None), {})
        self.assertEqual(module.safe_wan_events("bad"), [])


if __name__ == "__main__":
    unittest.main()
