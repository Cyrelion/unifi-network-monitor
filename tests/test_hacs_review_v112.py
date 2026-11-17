"""Regression checks for the v1.1.2 HACS review fixes.

Extracting the login method allows testing its real control flow without a
Home Assistant installation or a live UniFi gateway.
"""
from __future__ import annotations

import ast
import asyncio
import logging
from pathlib import Path
import time
import unittest

BASE = Path(__file__).resolve().parents[1] / "custom_components" / "unifi_network_monitor"


class UniFiAuthError(Exception):
    pass


class UniFiRequestError(Exception):
    pass


class ClientError(Exception):
    pass


class FakeResponse:
    def __init__(self, status: int, body: str = "") -> None:
        self.status = status
        self.body = body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def text(self):
        return self.body


class FakeSession:
    def __init__(self, *statuses: int) -> None:
        self.statuses = list(statuses)
        self.posts = 0

    def post(self, *args, **kwargs):
        status = self.statuses[self.posts]
        self.posts += 1
        return FakeResponse(status)


class FakeClient:
    def __init__(self, *statuses: int) -> None:
        self._authenticated_until = 0.0
        self._login_backoff_until = 0.0
        self._login_lock = asyncio.Lock()
        self._base_url = "https://gateway.invalid"
        self.username = "tester"
        self.password = "test-password"
        self.timeout = 5
        self.session = FakeSession(*statuses)
        self.cookies_cleared = 0

    def _clear_cookies(self):
        self.cookies_cleared += 1


def _method(path: str, cls_name: str, method: str, env: dict):
    tree = ast.parse((BASE / path).read_text(encoding="utf-8"))
    cls = next(x for x in tree.body if isinstance(x, ast.ClassDef) and x.name == cls_name)
    fn = next(x for x in cls.body if isinstance(x, ast.AsyncFunctionDef) and x.name == method)
    # The source method is executed, not a reimplementation.
    module = ast.Module(body=[fn], type_ignores=[])
    ast.fix_missing_locations(module)
    exec(compile(module, path, "exec"), env)
    return env[method]


class TestLoginErrorClassification(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.login = staticmethod(
            _method(
                "api.py",
                "UniFiNetworkMonitorClient",
                "async_login",
                {
                    "asyncio": asyncio,
                    "time": time,
                    "_LOGGER": logging.getLogger(__name__),
                    "UniFiAuthError": UniFiAuthError,
                    "UniFiRequestError": UniFiRequestError,
                    "ClientError": ClientError,
                    "TimeoutError": TimeoutError,
                },
            )
        )

    def test_invalid_credentials_trigger_auth_error(self):
        for status in (401, 403):
            with self.subTest(status=status):
                client = FakeClient(status, status)
                with self.assertRaises(UniFiAuthError):
                    asyncio.run(self.login(client))
                self.assertEqual(client.session.posts, 2 if status == 403 else 1)

    def test_rate_limit_stays_transient_and_enforces_backoff(self):
        client = FakeClient(429)
        with self.assertRaises(UniFiRequestError):
            asyncio.run(self.login(client))
        self.assertGreater(client._login_backoff_until, time.time())
        with self.assertRaises(UniFiRequestError):
            asyncio.run(self.login(client))
        self.assertEqual(client.session.posts, 1)

    def test_gateway_error_is_not_reauth(self):
        client = FakeClient(503)
        with self.assertRaises(UniFiRequestError):
            asyncio.run(self.login(client))

    def test_login_uses_valid_session_and_refreshes_expired_one(self):
        client = FakeClient(200)
        client._authenticated_until = time.time() + 60
        asyncio.run(self.login(client))
        self.assertEqual(client.session.posts, 0)
        client._authenticated_until = time.time() - 60
        asyncio.run(self.login(client))
        self.assertEqual(client.session.posts, 1)


class TestCoordinatorAuthentication(unittest.TestCase):
    def test_cached_data_does_not_suppress_reauth(self):
        tree = ast.parse((BASE / "coordinator.py").read_text(encoding="utf-8"))
        cls = next(x for x in tree.body if isinstance(x, ast.ClassDef) and x.name == "UniFiNetworkMonitorCoordinator")
        fn = next(x for x in cls.body if isinstance(x, ast.AsyncFunctionDef) and x.name == "_async_update_data")
        auth_handler = next(h for node in ast.walk(fn) if isinstance(node, ast.Try)
                            for h in node.handlers
                            if isinstance(h.type, ast.Name) and h.type.id == "UniFiAuthError")
        self.assertTrue(any(isinstance(node, ast.Raise) for node in ast.walk(auth_handler)))
        self.assertFalse(any(isinstance(node, ast.If) and isinstance(node.test, ast.Attribute)
                             and node.test.attr == "data" for node in ast.walk(auth_handler)))


class TestWebhookLifecycleAndPrivacy(unittest.TestCase):
    def test_first_refresh_precedes_webhook_register(self):
        text = (BASE / "__init__.py").read_text(encoding="utf-8")
        self.assertLess(text.index("await coordinator.async_config_entry_first_refresh()"),
                        text.index("await async_register_webhook(hass, entry, coordinator)"))

    def test_webhook_cleanup_registered_with_entry(self):
        text = (BASE / "webhook.py").read_text(encoding="utf-8")
        self.assertIn("entry.async_on_unload(lambda: webhook.async_unregister(hass, webhook_id))", text)
        self.assertNotIn('webhook %s", webhook_id', text)

    def test_diagnostics_only_redacts_vpn_names(self):
        text = (BASE / "diagnostics.py").read_text(encoding="utf-8")
        tree = ast.parse(text)
        target = next(n for n in tree.body if isinstance(n, ast.Assign)
                      and any(isinstance(x, ast.Name) and x.id == "TO_REDACT" for x in n.targets))
        redact = ast.literal_eval(target.value)
        self.assertTrue({"user", "name", "display_name", "label", "connected_users"}.issubset(redact))
        sensor = (BASE / "sensor.py").read_text(encoding="utf-8")
        self.assertIn('"remote_users": safe_vpn_rows(remote_rows)', sensor)
        self.assertIn('"teleport_users": safe_vpn_rows(teleport_rows)', sensor)


if __name__ == "__main__":
    unittest.main()
