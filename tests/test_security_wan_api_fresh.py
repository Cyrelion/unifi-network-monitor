"""Guard against complete metadata exposure in WAN API Fresh binary sensor."""
from __future__ import annotations

import ast
import importlib.util
from pathlib import Path
import unittest

BASE = Path(__file__).resolve().parents[1] / "custom_components" / "unifi_network_monitor"
spec = importlib.util.spec_from_file_location("security_wan_api_fresh", BASE / "security.py")
assert spec is not None and spec.loader is not None
security = importlib.util.module_from_spec(spec)
spec.loader.exec_module(security)


class TestWanApiFreshMetadata(unittest.TestCase):
    def test_public_metadata_strips_diagnostics_and_nested_values(self):
        meta = {"stale": False, "wanconf_http": "200", "stale_error": "SECRET_MARKER",
                "host": "SECRET_MARKER", "site": "SECRET_MARKER",
                "unexpected": {"password": "SECRET_MARKER"}}
        safe = security.safe_public_meta(meta)
        self.assertEqual(safe, {"stale": False, "wanconf_http": "200"})
        self.assertNotIn("SECRET_MARKER", str(safe))

    def test_binary_sensor_wan_api_fresh_uses_public_filter(self):
        tree = ast.parse((BASE / "binary_sensor.py").read_text(encoding="utf-8"))
        matches = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
                continue
            if node.func.id != "UniFiBinarySensorDescription":
                continue
            args = {item.arg: item.value for item in node.keywords}
            key = args.get("key")
            if isinstance(key, ast.Constant) and key.value == "wan_api_fresh":
                matches.append(args)
        self.assertEqual(len(matches), 1)
        attr_fn = matches[0]["attrs_fn"]
        self.assertIsInstance(attr_fn, ast.Lambda)
        self.assertIsInstance(attr_fn.body, ast.Dict)
        self.assertEqual(len(attr_fn.body.values), 1)
        call = attr_fn.body.values[0]
        self.assertIsInstance(call, ast.Call)
        self.assertIsInstance(call.func, ast.Name)
        self.assertEqual(call.func.id, "safe_public_meta")


if __name__ == "__main__":
    unittest.main()
