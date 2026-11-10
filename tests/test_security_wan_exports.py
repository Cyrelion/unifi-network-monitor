"""Regression checks for actual WAN raw sensor attribute wiring.

These tests inspect the sensor description code without importing Home Assistant.
Full HA integration tests remain necessary before release.
"""
from __future__ import annotations

import ast
import importlib.util
from pathlib import Path
import unittest

BASE = Path(__file__).resolve().parents[1] / "custom_components" / "unifi_network_monitor"
SPEC = importlib.util.spec_from_file_location("local_security", BASE / "security.py")
assert SPEC is not None and SPEC.loader is not None
security = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(security)


class TestWanSensorExports(unittest.TestCase):
    def test_sensor_description_filters_raw_config_data_and_metadata(self):
        source = (BASE / "sensor.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        found = False
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if not isinstance(node.func, ast.Name) or node.func.id != "UniFiSensorDescription":
                continue
            kwargs = {kw.arg: kw.value for kw in node.keywords}
            key = kwargs.get("key")
            if not isinstance(key, ast.Constant) or key.value != "wan_config_raw":
                continue
            attrs = kwargs.get("attrs_fn")
            self.assertIsInstance(attrs, ast.Lambda)
            self.assertIsInstance(attrs.body, ast.Dict)
            values = dict(zip((k.value for k in attrs.body.keys), attrs.body.values))
            self.assertEqual(values["data"].func.id, "safe_wan_config_rows")
            self.assertEqual(values["meta"].func.id, "safe_public_meta")
            found = True
        self.assertTrue(found, "Expected wan_config_raw sensor description")

    def test_filtered_metadata_never_contains_untrusted_fields(self):
        meta = {"ts": 123, "stale": False, "host": "router.local",
                "stale_error": "SECRET", "api_key": "SECRET", "nested": {"password": "SECRET"}}
        result = security.safe_public_meta(meta)
        self.assertEqual(result, {"ts": 123, "stale": False})


if __name__ == "__main__":
    unittest.main()
