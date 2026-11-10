"""Security regression tests for WAN attributes and nested sensitive data."""
from __future__ import annotations

import importlib.util
import pathlib
import unittest

MODULE_PATH = pathlib.Path(__file__).resolve().parents[1] / "custom_components" / "unifi_network_monitor" / "security.py"
spec = importlib.util.spec_from_file_location("unifi_wan_security", MODULE_PATH)
security = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(security)


class TestWanConfigAllowlist(unittest.TestCase):
    def test_preserves_routing_fields(self) -> None:
        data = {"purpose": "wan", "wan_networkgroup": "WAN2", "name": "Starlink",
                "wan_type": "dhcp", "wan_failover_priority": 20,
                "wan_load_balance_type": "weighted", "wan_load_balance_weight": 50}
        self.assertEqual(security.safe_wan_config(data), data)

    def test_drops_secrets_regardless_of_case_or_nesting(self) -> None:
        data = {"name": "WAN1", "password": "SECRET_1", "PPPoEPassword": "SECRET_2",
                "token": "SECRET_3", "credentials": {"secret": "SECRET_4"},
                "nested": [{"privateKey": "SECRET_5"}], "api_key": "SECRET_6"}
        sanitized = security.safe_wan_config(data)
        self.assertEqual(sanitized, {"name": "WAN1"})
        self.assertNotIn("SECRET_", str(sanitized))

    def test_drops_nested_value_even_under_allowed_key(self) -> None:
        data = {"name": {"password": "SECRET"}, "wan_load_balance_weight": [1, 2],
                "wan_networkgroup": "WAN"}
        self.assertEqual(security.safe_wan_config(data), {"wan_networkgroup": "WAN"})

    def test_drops_malformed_rows(self) -> None:
        self.assertEqual(security.safe_wan_config_rows([{"name": "WAN"}, "bad", None]),
                         [{"name": "WAN"}])


if __name__ == "__main__":
    unittest.main()
