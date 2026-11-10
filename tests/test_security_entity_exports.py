"""Check event text redaction and entity exposure wiring without HA imports."""
from __future__ import annotations
import ast
import importlib.util
from pathlib import Path
import unittest

BASE = Path(__file__).resolve().parents[1] / "custom_components" / "unifi_network_monitor"
spec = importlib.util.spec_from_file_location("private_security_review", BASE / "security.py")
assert spec and spec.loader
security = importlib.util.module_from_spec(spec)
spec.loader.exec_module(security)

class TestEntitySecurity(unittest.TestCase):
    def test_event_payload_strings_are_not_exposed(self):
        event = {"wan": "WAN1", "kind": "other", "event": "High Latency Detected",
                 "message": "SECRET_MARKER", "wan_name": "SECRET_MARKER",
                 "wan_isp": "SECRET_MARKER", "notify_text": "SECRET_MARKER",
                 "query": {"password": "SECRET_MARKER"}}
        output = security.safe_wan_event(event)
        self.assertEqual(output["event"], "High Latency Detected")
        self.assertEqual(output["wan_name"], "WAN1")
        self.assertNotIn("SECRET_MARKER", str(output))

    def test_old_history_is_canonicalized(self):
        event = {"wan": "WAN2", "kind": "down", "event": "SECRET_MARKER",
                 "message": "SECRET_MARKER", "ts": "2026-11-09T18:00:00+01:00"}
        output = security.safe_wan_events([event])[0]
        self.assertEqual(output["event"], "WAN Disconnected")
        self.assertEqual(output["ts"], "2026-11-09T18:00:00+01:00")
        self.assertNotIn("SECRET_MARKER", str(output))

    def test_sensor_description_uses_allowlisted_data_and_metadata(self):
        tree = ast.parse((BASE / "sensor.py").read_text(encoding="utf-8"))
        found = {}
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
                continue
            if node.func.id != "UniFiSensorDescription":
                continue
            fields = {kw.arg: kw.value for kw in node.keywords}
            key = fields.get("key")
            if isinstance(key, ast.Constant) and isinstance(key.value, str):
                found[key.value] = fields
        attrs = found["wan_config_raw"]["attrs_fn"]
        self.assertIsInstance(attrs, ast.Lambda)
        values = {k.value: v for k,v in zip(attrs.body.keys, attrs.body.values)}
        self.assertEqual(values["data"].func.id, "safe_wan_config_rows")
        self.assertEqual(values["meta"].func.id, "safe_public_meta")
        self.assertEqual(found["wan_health_raw"]["attrs_fn"].body.func.id, "safe_wan_health")
        self.assertEqual(found["vpn_status_raw"]["attrs_fn"].id, "_vpn_raw_attrs")

    def test_webhook_ingest_and_history_use_sanitizer(self):
        webhook = (BASE / "webhook.py").read_text(encoding="utf-8")
        coordinator = (BASE / "coordinator.py").read_text(encoding="utf-8")
        self.assertIn("safe_wan_event(build_wan_event(", webhook)
        self.assertIn("self._wan_events = safe_wan_events(events)", coordinator)
        self.assertIn("self._wan_events = [safe_wan_event(event)", coordinator)

if __name__ == "__main__":
    unittest.main()
