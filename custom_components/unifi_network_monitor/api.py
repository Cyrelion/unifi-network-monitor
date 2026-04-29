"""Async client for UniFi Network Monitor."""
from __future__ import annotations

import asyncio
import ipaddress
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any

from aiohttp import ClientError, ClientResponseError, ClientSession, DummyCookieJar

from .const import (
    DEFAULT_SNMP_PORT,
    DEFAULT_SNMP_TIMEOUT,
    DEFAULT_TELEPORT_CLIENT_MATCHERS,
    DEFAULT_TELEPORT_PREFIX,
    DEFAULT_TIMEOUT,
)
from .snmp_client import SnmpError, normalize_oid_map, snmp_get

_LOGGER = logging.getLogger(__name__)


class UniFiNetworkMonitorError(Exception):
    """Base error for UniFi Network Monitor."""


class UniFiAuthError(UniFiNetworkMonitorError):
    """Authentication failed."""


class UniFiRequestError(UniFiNetworkMonitorError):
    """Request failed."""


@dataclass(slots=True)
class UniFiNetworkMonitorClient:
    """Very small async client for local UniFi OS Network endpoints."""

    session: ClientSession
    host: str
    username: str
    password: str
    site: str = "default"
    site_name: str = "Default"
    teleport_prefix: str = DEFAULT_TELEPORT_PREFIX
    teleport_client_matchers: str = DEFAULT_TELEPORT_CLIENT_MATCHERS
    api_key: str | None = None
    timeout: int = DEFAULT_TIMEOUT
    snmp_enabled: bool = False
    snmp_host: str = ""
    snmp_community: str = ""
    snmp_port: int = DEFAULT_SNMP_PORT
    snmp_timeout: int = DEFAULT_SNMP_TIMEOUT
    snmp_oids: dict[str, str] = field(default_factory=dict)
    _base_url: str = field(init=False, repr=False)
    _login_lock: asyncio.Lock = field(init=False, repr=False)
    _authenticated_until: float = field(init=False, default=0.0, repr=False)
    _login_backoff_until: float = field(init=False, default=0.0, repr=False)
    _last_snmp_counters: dict[str, int] = field(init=False, default_factory=dict, repr=False)
    _last_snmp_ts: float = field(init=False, default=0.0, repr=False)
    _last_vpn_probe_ts: float = field(init=False, default=0.0, repr=False)
    _last_vpn_probe: dict[str, Any] = field(init=False, default_factory=dict, repr=False)
    _teleport_matchers: list[str] = field(init=False, default_factory=list, repr=False)

    def __post_init__(self) -> None:
        """Normalize input and initialize runtime state."""
        self.host = self._clean_host(self.host)
        self.site = self.site or "default"
        self.site_name = self.site_name or "Default"
        self.teleport_prefix = self.teleport_prefix or DEFAULT_TELEPORT_PREFIX
        self.teleport_client_matchers = self.teleport_client_matchers or DEFAULT_TELEPORT_CLIENT_MATCHERS
        self._teleport_matchers = self._parse_matchers(self.teleport_client_matchers)
        self.api_key = self.api_key or None
        self.snmp_host = self._clean_host(self.snmp_host or self.host)
        self.snmp_community = (self.snmp_community or "").strip()
        self.snmp_enabled = bool(self.snmp_enabled and self.snmp_community)
        self.snmp_port = int(self.snmp_port or DEFAULT_SNMP_PORT)
        self.snmp_timeout = int(self.snmp_timeout or DEFAULT_SNMP_TIMEOUT)
        self.snmp_oids = normalize_oid_map(self.snmp_oids or {})
        self._base_url = f"https://{self.host}"
        self._login_lock = asyncio.Lock()
        self._authenticated_until = 0.0
        self._login_backoff_until = 0.0

    @staticmethod
    def _clean_host(host: str) -> str:
        """Return host without scheme or trailing slash."""
        value = host.strip()
        if value.startswith("https://"):
            value = value.removeprefix("https://")
        if value.startswith("http://"):
            value = value.removeprefix("http://")
        return value.strip("/")

    @staticmethod
    def _parse_matchers(value: str | None) -> list[str]:
        """Parse manually configured Teleport client matchers.

        UniFi sometimes exposes Teleport sessions as completely ordinary clients.
        When neither the Integration API nor a Teleport IP range marks the row,
        users can provide stable client names, hostnames, MACs or IP fragments.
        Values may be comma, semicolon or newline separated.
        """
        if not value:
            return []
        parts = re.split(r"[,;\n\r]+", str(value))
        matchers: list[str] = []
        for part in parts:
            item = part.strip().lower()
            if not item:
                continue
            compact_mac = re.sub(r"[^0-9a-f]", "", item)
            if len(compact_mac) == 12 and re.fullmatch(r"[0-9a-f]{12}", compact_mac):
                item = compact_mac
            if item not in matchers:
                matchers.append(item)
        return matchers


    def _clear_cookies(self) -> None:
        """Clear local UniFi cookies before a forced relogin."""
        cookie_jar = getattr(self.session, "cookie_jar", None)
        if cookie_jar is not None:
            try:
                cookie_jar.clear()
            except Exception:  # noqa: BLE001
                _LOGGER.debug("Could not clear UniFi session cookies", exc_info=True)

    async def async_login(self, *, force: bool = False) -> None:
        """Authenticate against UniFi OS.

        UniFi OS can temporarily rate-limit local logins. When that happens we
        enter a short backoff window instead of retrying every coordinator
        cycle. The old Bash package had the same idea via LOGIN_BACKOFF_SECONDS.
        """
        now = time.time()
        if not force and self._authenticated_until > 0:
            return

        async with self._login_lock:
            now = time.time()
            if not force and self._authenticated_until > 0:
                return

            if self._login_backoff_until > now:
                remaining = int(self._login_backoff_until - now)
                raise UniFiAuthError(
                    f"login_backoff_active; retrying login in about {remaining}s"
                )

            url = f"{self._base_url}/api/auth/login"
            payload = {"username": self.username, "password": self.password}

            if force:
                self._clear_cookies()

            try:
                last_status = 0
                last_text = ""
                for attempt in range(2):
                    async with self.session.post(url, json=payload, timeout=self.timeout) as response:
                        text = await response.text()
                        last_status = response.status
                        last_text = text
                        if response.status == 200:
                            self._login_backoff_until = 0.0
                            # UniFi OS cookies normally live longer than our polling interval.
                            # Keep using the current cookie until a critical endpoint returns
                            # 401 or 403 instead of proactively logging in again every few
                            # minutes. This matches the old cache based package more closely.
                            self._authenticated_until = time.time() + 7 * 24 * 60 * 60
                            return
                        if response.status == 429:
                            self._login_backoff_until = time.time() + 15 * 60
                            raise UniFiAuthError(
                                f"UniFi OS rate limited the login request: {text[:300]}"
                            )
                        if response.status == 403 and attempt == 0:
                            self._clear_cookies()
                            await asyncio.sleep(0)
                            continue
                        break

                raise UniFiAuthError(
                    f"Login failed with HTTP {last_status}: {last_text[:300]}"
                )
            except (ClientError, TimeoutError, asyncio.TimeoutError) as err:
                raise UniFiAuthError(f"Login request failed: {err}") from err

    async def async_get_json(
        self,
        path: str,
        *,
        retry_auth: bool = True,
        raise_for_status: bool = True,
    ) -> tuple[int, Any]:
        """GET a UniFi OS Network endpoint and return HTTP status plus JSON body.

        UniFi endpoints are not all equally available on every gateway or firmware
        combination. The old Bash implementation treated non-critical endpoint
        failures as empty data and kept the integration alive; this method can
        do the same via raise_for_status=False.
        """
        await self.async_login()
        url = f"{self._base_url}{path}"
        try:
            async with self.session.get(url, timeout=self.timeout) as response:
                if response.status in (401, 403) and retry_auth:
                    self._authenticated_until = 0
                    await self.async_login(force=True)
                    return await self.async_get_json(
                        path,
                        retry_auth=False,
                        raise_for_status=raise_for_status,
                    )

                text = await response.text()
                body: Any = {}
                if text:
                    try:
                        body = await response.json(content_type=None)
                    except Exception as err:  # noqa: BLE001
                        if raise_for_status:
                            raise UniFiRequestError(
                                f"GET {path} returned invalid JSON with HTTP {response.status}: "
                                f"{text[:300]}"
                            ) from err
                        body = {}

                if raise_for_status and response.status >= 400:
                    raise UniFiRequestError(
                        f"GET {path} failed with HTTP {response.status}: {text[:300]}"
                    )

                return response.status, body
        except (ClientResponseError, ClientError, TimeoutError, asyncio.TimeoutError) as err:
            if not raise_for_status:
                _LOGGER.debug("Optional UniFi endpoint GET %s failed: %s", path, err)
                return 0, {}
            raise UniFiRequestError(f"GET {path} failed: {err}") from err

    async def async_get_integration_json(self, path: str) -> tuple[int, Any]:
        """GET a UniFi Network Integration API endpoint without session cookies.

        The old working shell script calls curl with only the X-API-KEY header.
        Reusing Home Assistant's authenticated UniFi OS session can silently add
        OS session cookies to the request. Some UniFi Network versions then
        reject the Integration API call with 401/403, even when the API key is
        correct. Use a short-lived aiohttp session with DummyCookieJar while
        reusing the HA connector/SSL settings.
        """
        if not self.api_key:
            return 0, {"_integration_auth_method": "none", "error": "missing_api_key"}

        url = f"{self._base_url}{path}"
        headers = {
            "Accept": "application/json",
            "X-API-KEY": self.api_key,
        }

        session_kwargs: dict[str, Any] = {"cookie_jar": DummyCookieJar()}
        connector = getattr(self.session, "connector", None)
        if connector is not None and not getattr(connector, "closed", False):
            session_kwargs["connector"] = connector
            session_kwargs["connector_owner"] = False

        try:
            async with ClientSession(**session_kwargs) as no_cookie_session:
                async with no_cookie_session.get(
                    url,
                    headers=headers,
                    timeout=max(self.timeout, 20),
                ) as response:
                    text = await response.text()
                    body: Any = {}
                    if text:
                        try:
                            body = await response.json(content_type=None)
                        except Exception:  # noqa: BLE001
                            body = {"raw": text[:500]}
                    if isinstance(body, dict):
                        body = dict(body)
                        body.setdefault("_integration_auth_method", "x-api-key-dummy-cookiejar")
                        body.setdefault("_integration_body_keys", sorted(str(k) for k in body.keys())[:40])
                    return response.status, body
        except (ClientError, TimeoutError, asyncio.TimeoutError) as err:
            _LOGGER.debug("Integration API request failed: %s", err)
            return 0, {"_integration_auth_method": "x-api-key-dummy-cookiejar", "error": str(err)}


    async def async_fetch_all(self) -> dict[str, Any]:
        """Fetch the same core data as the former udm_cache_update.sh script."""
        site = self.site
        paths = {
            "wanconf": f"/proxy/network/api/s/{site}/rest/networkconf",
            "health": f"/proxy/network/api/s/{site}/stat/health",
            "vpn": f"/proxy/network/api/s/{site}/stat/remoteuservpn",
            "clients": f"/proxy/network/api/s/{site}/stat/sta",
        }

        async def _fetch_network_endpoints() -> tuple[int, Any, int, Any, int, Any, int, Any]:
            wanconf_http, wanconf_body = await self.async_get_json(
                paths["wanconf"],
                retry_auth=False,
                raise_for_status=False,
            )
            health_http, health_body = await self.async_get_json(
                paths["health"],
                retry_auth=False,
                raise_for_status=False,
            )
            vpn_http, vpn_body = await self.async_get_json(
                paths["vpn"],
                retry_auth=False,
                raise_for_status=False,
            )
            clients_http, clients_body = await self.async_get_json(
                paths["clients"],
                retry_auth=False,
                raise_for_status=False,
            )
            return (
                wanconf_http,
                wanconf_body,
                health_http,
                health_body,
                vpn_http,
                vpn_body,
                clients_http,
                clients_body,
            )

        await self.async_login()
        (
            wanconf_http,
            wanconf_body,
            health_http,
            health_body,
            vpn_http,
            vpn_body,
            clients_http,
            clients_body,
        ) = await _fetch_network_endpoints()

        # Only the critical WAN endpoints should trigger a forced relogin. Some
        # optional endpoints can return 401/403 depending on UniFi version,
        # permissions or VPN feature state. Treating those as session expiry made
        # the integration login-loop and eventually hit UniFi rate limiting.
        if wanconf_http in (401, 403) or health_http in (401, 403):
            self._authenticated_until = 0.0
            await self.async_login(force=True)
            (
                wanconf_http,
                wanconf_body,
                health_http,
                health_body,
                vpn_http,
                vpn_body,
                clients_http,
                clients_body,
            ) = await _fetch_network_endpoints()

        integration_sites_http = 0
        integration_clients_http = 0
        integration_site_id = ""
        integration_clients_body: Any = {}
        integration_sites_body: Any = {}

        integration_base_path = ""
        integration_auth_method = "none"
        integration_clients_rows: list[dict[str, Any]] = []
        integration_clients_pages = 0
        integration_clients_total: int | None = None
        integration_clients_shape: dict[str, Any] = {}
        if self.api_key:
            for base_path in ("/proxy/network/integration/v1", "/proxy/network/integrations/v1"):
                integration_sites_http, integration_sites_body = await self.async_get_integration_json(
                    f"{base_path}/sites"
                )
                if isinstance(integration_sites_body, dict):
                    integration_auth_method = str(integration_sites_body.get("_integration_auth_method") or integration_auth_method or "none")
                if integration_sites_http == 200:
                    integration_base_path = base_path
                    integration_site_id = self._extract_integration_site_id(integration_sites_body)
                    break
                if integration_sites_http not in (401, 403, 404):
                    break

            if integration_site_id and integration_base_path:
                # Match the old working Bash updater first: unfiltered client list.
                # Then keep paginating if UniFi ignores the high limit or returns a
                # total count. Teleport rows can otherwise hide on a later page.
                limit = 1000
                offset = 0
                for _page in range(10):
                    page_http, page_body = await self.async_get_integration_json(
                        f"{integration_base_path}/sites/{integration_site_id}/clients?offset={offset}&limit={limit}"
                    )
                    integration_clients_http = page_http
                    integration_clients_body = page_body
                    integration_clients_pages += 1
                    if isinstance(page_body, dict):
                        integration_auth_method = str(page_body.get("_integration_auth_method") or integration_auth_method or "none")
                        if not integration_clients_shape:
                            integration_clients_shape = self._body_debug_shape(page_body)
                        integration_clients_total = self._extract_total_count(page_body)
                    page_rows = self._rows(page_body)
                    integration_clients_rows.extend(page_rows)
                    if page_http != 200 or not page_rows:
                        break
                    if integration_clients_total is not None and len(integration_clients_rows) >= integration_clients_total:
                        break
                    if len(page_rows) < limit:
                        break
                    offset += limit

                # If the exact legacy shape fails on a future UniFi version, keep a
                # harmless fallback for newer pluralized endpoint variants.
                if integration_clients_http in (404, 405) and integration_base_path == "/proxy/network/integration/v1":
                    integration_clients_rows = []
                    integration_clients_pages = 0
                    limit = 1000
                    offset = 0
                    for _page in range(10):
                        page_http, page_body = await self.async_get_integration_json(
                            f"/proxy/network/integrations/v1/sites/{integration_site_id}/clients?offset={offset}&limit={limit}"
                        )
                        integration_clients_http = page_http
                        integration_clients_body = page_body
                        integration_clients_pages += 1
                        if isinstance(page_body, dict):
                            integration_auth_method = str(page_body.get("_integration_auth_method") or integration_auth_method or "none")
                            if not integration_clients_shape:
                                integration_clients_shape = self._body_debug_shape(page_body)
                            integration_clients_total = self._extract_total_count(page_body)
                        page_rows = self._rows(page_body)
                        integration_clients_rows.extend(page_rows)
                        if page_http != 200 or not page_rows:
                            break
                        if integration_clients_total is not None and len(integration_clients_rows) >= integration_clients_total:
                            break
                        if len(page_rows) < limit:
                            break
                        offset += limit

                integration_clients_rows = self._dedupe_source_rows(integration_clients_rows)


        client_inventory = await self.async_fetch_local_client_inventory(clients_body)

        wan_config = self._rows(wanconf_body)
        wan_config = [row for row in wan_config if row.get("purpose") == "wan"]

        health_rows = self._rows(health_body)
        wan_health_raw = [row for row in health_rows if row.get("subsystem") == "wan"]
        vpn_health_raw = [row for row in health_rows if row.get("subsystem") == "vpn"]
        vpn_raw = self._rows(vpn_body)
        clients_raw = client_inventory.get("rows", self._rows(clients_body))
        clients_integration_raw = integration_clients_rows or self._rows(integration_clients_body)

        meta = {
            "host": self.host,
            "site": self.site,
            "site_name": self.site_name,
            "integration_site_id": integration_site_id,
            "integration_base_path": integration_base_path,
            "integration_auth_method": integration_auth_method,
            "ts": int(time.time()),
            "stale": False,
            "wanconf_http": str(wanconf_http),
            "health_http": str(health_http),
            "vpn_http": str(vpn_http),
            "clients_http": str(clients_http),
            "client_inventory_row_count": str(client_inventory.get("row_count", 0)),
            "client_inventory_teleport_range_match_count": str(client_inventory.get("teleport_range_match_count", 0)),
            "client_inventory_http": ",".join(
                f"{name}:{info.get('http')}" for name, info in (client_inventory.get("endpoints") or {}).items()
            ),
            "integration_sites_http": str(integration_sites_http),
            "integration_clients_http": str(integration_clients_http),
            "integration_clients_pages": str(integration_clients_pages),
            "integration_clients_total": str(integration_clients_total or ""),
            "integration_clients_shape": integration_clients_shape,
            "integration_clients_row_count": str(len(integration_clients_rows)),
            "teleport_prefix": self.teleport_prefix,
            "login_backoff_active": self._login_backoff_until > time.time(),
            "login_backoff_until": int(self._login_backoff_until) if self._login_backoff_until else 0,
            "authenticated_until": int(self._authenticated_until) if self._authenticated_until else 0,
        }
        cached_vpn_summary = self._vpn_summary(vpn_raw, vpn_health_raw, vpn_http)
        vpn_probe = await self.async_fetch_vpn_probe()
        vpn_status = self._vpn_status(
            vpn_raw=vpn_raw,
            vpn_health_raw=vpn_health_raw,
            clients_raw=clients_raw,
            clients_integration_raw=clients_integration_raw,
            client_inventory=client_inventory,
            vpn_probe=vpn_probe,
            cached_summary=cached_vpn_summary,
            meta=meta,
        )

        traffic = await self.async_fetch_snmp_traffic()

        return {
            "meta": meta,
            "wan_config": wan_config,
            "wan_health_raw": wan_health_raw,
            "vpn_summary": vpn_status["summary"],
            "vpn_status": vpn_status,
            "traffic": traffic,
        }

    async def async_fetch_local_client_inventory(self, stat_sta_body: Any) -> dict[str, Any]:
        """Fetch and merge local UniFi client inventories.

        Teleport clients can show up in the UniFi UI as ordinary clients, but
        depending on UniFi Network version they are not always returned by
        /stat/sta. Probe a small set of legacy client endpoints and merge their
        rows so Teleport CIDR matching can work without the official
        Integration API.
        """
        site = self.site
        paths = {
            "stat_sta": f"/proxy/network/api/s/{site}/stat/sta",
            "stat_alluser": f"/proxy/network/api/s/{site}/stat/alluser",
            "stat_user": f"/proxy/network/api/s/{site}/stat/user",
            "rest_user": f"/proxy/network/api/s/{site}/rest/user",
            "rest_sta": f"/proxy/network/api/s/{site}/rest/sta",
            "stat_clients": f"/proxy/network/api/s/{site}/stat/clients",
        }

        endpoints: dict[str, Any] = {}
        all_rows: list[dict[str, Any]] = []

        initial_rows = self._rows(stat_sta_body)
        endpoints["stat_sta"] = {
            "path": paths["stat_sta"],
            "http": "cached",
            "row_count": len(initial_rows),
            "teleport_range_match_count": len([row for row in initial_rows if self._row_matches_teleport_range(row)]),
        }
        all_rows.extend(self._tag_source_rows(initial_rows, "stat_sta"))

        for key, path in paths.items():
            if key == "stat_sta":
                continue
            status, body = await self.async_get_json(path, retry_auth=False, raise_for_status=False)
            rows = self._rows(body)
            if not rows:
                rows = self._candidate_rows(body)
            tagged = self._tag_source_rows(rows, key)
            endpoints[key] = {
                "path": path,
                "http": str(status),
                "row_count": len(rows),
                "teleport_range_match_count": len([row for row in tagged if self._row_matches_teleport_range(row)]),
                "top_keys": sorted({str(k) for row in rows[:5] for k in row.keys()})[:30],
            }
            if status == 200:
                all_rows.extend(tagged)

        merged = self._dedupe_source_rows(all_rows)
        range_matches = [row for row in merged if self._row_matches_teleport_range(row)]
        return {
            "endpoints": endpoints,
            "rows": merged,
            "row_count": len(merged),
            "teleport_range_match_count": len(range_matches),
            "teleport_range_matches": self._client_debug_snapshot(range_matches, limit=20),
        }

    @staticmethod
    def _tag_source_rows(rows: list[dict[str, Any]], source: str) -> list[dict[str, Any]]:
        """Return shallow-copied rows tagged with their local API source."""
        tagged: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            item.setdefault("_unm_source", source)
            tagged.append(item)
        return tagged

    def _row_matches_teleport_range(self, row: dict[str, Any]) -> bool:
        """Return True when any detected IP value matches the Teleport CIDR/prefix."""
        return any(self._matches_teleport_range(ip) for ip in self._pick_ips(row))

    async def async_fetch_vpn_probe(self) -> dict[str, Any]:
        """Probe optional legacy VPN endpoints for Teleport/WireGuard rows."""
        now = time.time()
        if self._last_vpn_probe and (now - self._last_vpn_probe_ts) < 120:
            cached = dict(self._last_vpn_probe)
            cached["cached"] = True
            cached["cache_age_seconds"] = int(now - self._last_vpn_probe_ts)
            return cached

        site = self.site
        paths = {
            "stat_vpn": f"/proxy/network/api/s/{site}/stat/vpn",
            "stat_remoteuservpn": f"/proxy/network/api/s/{site}/stat/remoteuservpn",
            "stat_teleport": f"/proxy/network/api/s/{site}/stat/teleport",
            "stat_wireguard": f"/proxy/network/api/s/{site}/stat/wireguard",
            "rest_vpn": f"/proxy/network/api/s/{site}/rest/vpn",
            "rest_vpnuser": f"/proxy/network/api/s/{site}/rest/vpnuser",
            "rest_vpn_user": f"/proxy/network/api/s/{site}/rest/vpn-user",
            "rest_wireguard": f"/proxy/network/api/s/{site}/rest/wireguard",
            "rest_wireguard_peer": f"/proxy/network/api/s/{site}/rest/wireguard-peer",
        }
        endpoints: dict[str, Any] = {}
        all_rows: list[dict[str, Any]] = []
        for key, path in paths.items():
            status, body = await self.async_get_json(path, retry_auth=False, raise_for_status=False)
            rows = self._candidate_rows(body)
            matches = [
                row for row in rows
                if self._is_teleport(row, source="probe") or self._looks_like_vpn_session(row)
            ]
            endpoints[key] = {
                "path": path,
                "http": str(status),
                "row_count": len(rows),
                "match_count": len(matches),
                "top_keys": sorted({str(k) for row in rows[:5] for k in row.keys()})[:30],
                "matches": self._client_debug_snapshot(matches, limit=6),
            }
            all_rows.extend(matches)

        result = {
            "cached": False,
            "ts": int(now),
            "endpoints": endpoints,
            "rows": self._dedupe_source_rows(all_rows),
        }
        self._last_vpn_probe = result
        self._last_vpn_probe_ts = now
        return result

    @classmethod
    def _candidate_rows(cls, body: Any, *, depth: int = 0) -> list[dict[str, Any]]:
        """Return possible client/session rows from arbitrary UniFi JSON."""
        if depth > 5 or body in (None, ""):
            return []
        if isinstance(body, list):
            rows: list[dict[str, Any]] = []
            for item in body[:500]:
                rows.extend(cls._candidate_rows(item, depth=depth + 1))
            return rows
        if isinstance(body, dict):
            interesting = {
                "ip", "ipAddress", "remote_ip", "remoteIp", "tunnel_ip", "tunnelIp",
                "virtual_ip", "virtualIp", "username", "user", "state", "status",
                "connected", "connectedAt", "type", "vpnType", "clientType",
                "connectionType", "network", "name", "hostname", "mac", "macAddress",
            }
            keys = set(body.keys())
            rows: list[dict[str, Any]] = []
            if keys & interesting:
                rows.append(body)
            for key in ("data", "items", "results", "clients", "content", "vpn", "users", "sessions", "peers"):
                value = body.get(key)
                if isinstance(value, (list, dict)):
                    rows.extend(cls._candidate_rows(value, depth=depth + 1))
            return rows
        return []

    @staticmethod
    def _dedupe_source_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Deduplicate raw source rows conservatively."""
        result: list[dict[str, Any]] = []
        seen: set[str] = set()
        for row in rows:
            key = str(row.get("id") or row.get("_id") or row.get("mac") or row.get("macAddress") or row.get("ip") or row.get("ipAddress") or row)
            if key in seen:
                continue
            seen.add(key)
            result.append(row)
        return result

    async def async_fetch_snmp_traffic(self) -> dict[str, Any]:
        """Fetch SNMP WAN octet counters and derive rates."""
        oid_map = normalize_oid_map(self.snmp_oids or {})
        base_meta: dict[str, Any] = {
            "host": self.snmp_host,
            "port": self.snmp_port,
            "enabled": self.snmp_enabled,
            "ts": int(time.time()),
            "oids": oid_map,
            "errors": [],
        }

        if not self.snmp_enabled:
            return {
                "enabled": False,
                "ok": False,
                "status": "disabled",
                "values": {},
                "rates_bps": {},
                "rates_mbps": {},
                "raw_gb": {},
                "totals": {},
                "meta": base_meta,
            }

        if not oid_map:
            base_meta["errors"] = ["No SNMP OIDs configured"]
            return {
                "enabled": True,
                "ok": False,
                "status": "error",
                "values": {},
                "rates_bps": {},
                "rates_mbps": {},
                "raw_gb": {},
                "totals": {},
                "meta": base_meta,
            }

        async def _read_one(key: str, oid: str) -> tuple[str, int | None, str | None]:
            try:
                result = await asyncio.to_thread(
                    snmp_get,
                    self.snmp_host,
                    self.snmp_port,
                    self.snmp_community,
                    oid,
                    float(self.snmp_timeout),
                )
                if result.value is None:
                    return key, None, f"{key}: empty value for {oid}"
                if not isinstance(result.value, int):
                    return key, None, f"{key}: non-integer value for {oid}"
                return key, int(result.value), None
            except SnmpError as err:
                return key, None, f"{key}: {err}"
            except Exception as err:  # noqa: BLE001
                return key, None, f"{key}: {err}"

        rows = await asyncio.gather(*(_read_one(key, oid) for key, oid in oid_map.items()))
        now_ts = time.time()
        values: dict[str, int] = {}
        errors: list[str] = []
        for key, value, error in rows:
            if error:
                errors.append(error)
            if value is not None:
                values[key] = value

        elapsed = now_ts - self._last_snmp_ts if self._last_snmp_ts else 0
        rates_bps: dict[str, float] = {}
        if elapsed > 0:
            for key, value in values.items():
                previous = self._last_snmp_counters.get(key)
                if previous is None:
                    rates_bps[key] = 0.0
                    continue
                delta = value - previous
                if delta < 0:
                    delta = 0
                rates_bps[key] = round(delta / elapsed, 3)
        else:
            rates_bps = {key: 0.0 for key in values}

        rates_mbps = {key: round((value * 8) / 1_000_000, 3) for key, value in rates_bps.items()}
        raw_gb = {key: round(value / 1_000_000_000, 6) for key, value in values.items()}
        resets = [
            key
            for key, value in values.items()
            if key in self._last_snmp_counters and value < self._last_snmp_counters[key]
        ]

        wan1_rx = rates_mbps.get("wan1_rx", 0.0)
        wan1_tx = rates_mbps.get("wan1_tx", 0.0)
        wan2_rx = rates_mbps.get("wan2_rx", 0.0)
        wan2_tx = rates_mbps.get("wan2_tx", 0.0)
        wan1_rx_gb = raw_gb.get("wan1_rx", 0.0)
        wan1_tx_gb = raw_gb.get("wan1_tx", 0.0)
        wan2_rx_gb = raw_gb.get("wan2_rx", 0.0)
        wan2_tx_gb = raw_gb.get("wan2_tx", 0.0)
        totals = {
            "wan1_rx_mbps": wan1_rx,
            "wan1_tx_mbps": wan1_tx,
            "wan2_rx_mbps": wan2_rx,
            "wan2_tx_mbps": wan2_tx,
            "wan1_total_mbps": round(wan1_rx + wan1_tx, 3),
            "wan2_total_mbps": round(wan2_rx + wan2_tx, 3),
            "internet_rx_mbps": round(wan1_rx + wan2_rx, 3),
            "internet_tx_mbps": round(wan1_tx + wan2_tx, 3),
            "internet_total_mbps": round(wan1_rx + wan1_tx + wan2_rx + wan2_tx, 3),
            "wan1_rx_gb": round(wan1_rx_gb, 6),
            "wan1_tx_gb": round(wan1_tx_gb, 6),
            "wan2_rx_gb": round(wan2_rx_gb, 6),
            "wan2_tx_gb": round(wan2_tx_gb, 6),
            "wan1_total_gb": round(wan1_rx_gb + wan1_tx_gb, 6),
            "wan2_total_gb": round(wan2_rx_gb + wan2_tx_gb, 6),
            "internet_rx_gb": round(wan1_rx_gb + wan2_rx_gb, 6),
            "internet_tx_gb": round(wan1_tx_gb + wan2_tx_gb, 6),
            "internet_total_gb": round(wan1_rx_gb + wan1_tx_gb + wan2_rx_gb + wan2_tx_gb, 6),
        }

        self._last_snmp_counters.update(values)
        if values:
            self._last_snmp_ts = now_ts

        ok = len(values) == len(oid_map) and not errors
        base_meta.update(
            {
                "ts": int(now_ts),
                "elapsed_seconds": round(elapsed, 3),
                "errors": errors,
                "resets": resets,
                "value_count": len(values),
                "expected_count": len(oid_map),
            }
        )
        return {
            "enabled": True,
            "ok": ok,
            "status": "ok" if ok else "partial" if values else "error",
            "values": values,
            "rates_bps": rates_bps,
            "rates_mbps": rates_mbps,
            "raw_gb": raw_gb,
            "totals": totals,
            "meta": base_meta,
        }

    def _extract_integration_site_id(self, body: Any) -> str:
        """Try to find the integration site id for the configured UniFi site."""
        wanted = {self.site, self.site_name}
        rows = self._rows(body)
        for row in rows:
            values = {
                str(row.get("id", "")),
                str(row.get("name", "")),
                str(row.get("internalReference", "")),
                str(row.get("siteId", "")),
                str(row.get("slug", "")),
            }
            if wanted & values:
                return str(row.get("id", ""))
        if rows:
            return str(rows[0].get("id", ""))
        return ""

    @classmethod
    def _extract_total_count(cls, body: Any) -> int | None:
        """Return pagination total count from common UniFi response shapes."""
        if not isinstance(body, dict):
            return None
        for key in (
            "totalCount", "total_count", "total", "count", "totalItems",
            "total_items", "itemCount", "item_count",
        ):
            value = body.get(key)
            try:
                if value not in (None, ""):
                    return int(value)
            except (TypeError, ValueError):
                pass
        for key in ("meta", "pagination", "page", "pageInfo"):
            value = body.get(key)
            if isinstance(value, dict):
                found = cls._extract_total_count(value)
                if found is not None:
                    return found
        return None

    @classmethod
    def _body_debug_shape(cls, body: Any) -> dict[str, Any]:
        """Small debug summary for API response shapes without dumping full clients."""
        if isinstance(body, list):
            return {"type": "list", "length": len(body)}
        if isinstance(body, dict):
            shape: dict[str, Any] = {"type": "dict", "keys": sorted(str(k) for k in body.keys())[:30]}
            for key in ("data", "items", "results", "clients", "content"):
                value = body.get(key)
                if isinstance(value, list):
                    shape[f"{key}_length"] = len(value)
                elif isinstance(value, dict):
                    shape[f"{key}_type"] = "dict"
                    shape[f"{key}_keys"] = sorted(str(k) for k in value.keys())[:20]
                    nested_rows = cls._rows(value)
                    if nested_rows:
                        shape[f"{key}_nested_row_count"] = len(nested_rows)
            total = cls._extract_total_count(body)
            if total is not None:
                shape["total_count"] = total
            return shape
        return {"type": type(body).__name__}

    @classmethod
    def _rows(cls, body: Any) -> list[dict[str, Any]]:
        """Return a list of dictionaries from UniFi's changing response shapes."""
        if isinstance(body, list):
            rows = body
        elif isinstance(body, dict):
            rows = []
            for key in ("data", "items", "results", "clients", "content"):
                value = body.get(key)
                if isinstance(value, list):
                    rows = value
                    break
                if isinstance(value, dict):
                    nested = cls._rows(value)
                    if nested:
                        rows = nested
                        break
        else:
            rows = []
        return [row for row in rows if isinstance(row, dict)]

    @staticmethod
    def _truthy(value: Any) -> bool:
        """Return whether a UniFi value should be considered true."""
        if value is True:
            return True
        if value in (False, None):
            return False
        return str(value).lower() in {"1", "true", "yes", "connected", "online", "up", "active"}

    @staticmethod
    def _falsy(value: Any) -> bool:
        """Return whether a UniFi value should be considered false."""
        if value is False:
            return True
        if value in (True, None):
            return False
        return str(value).lower() in {"0", "false", "no", "disconnected", "offline", "down", "inactive"}

    @staticmethod
    def _str_or_none(value: Any) -> str | None:
        """Return a non-empty string or None."""
        if value in (None, ""):
            return None
        return str(value)

    _IPV4_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")

    @classmethod
    def _extract_ip_values(cls, value: Any, *, depth: int = 0) -> list[str]:
        """Extract IP-looking values from UniFi's many client response shapes."""
        if depth > 4 or value in (None, ""):
            return []

        found: list[str] = []
        if isinstance(value, str):
            for candidate in cls._IPV4_RE.findall(value):
                try:
                    ipaddress.ip_address(candidate)
                except ValueError:
                    continue
                if candidate not in found:
                    found.append(candidate)
            return found

        if isinstance(value, bool):
            return []

        if isinstance(value, int):
            # Some APIs expose IPv4 values as unsigned 32-bit integers. Only
            # treat plausible private IPv4 integers as client IPs to avoid
            # confusing counters/timestamps with addresses.
            try:
                candidate = str(ipaddress.ip_address(value))
            except ValueError:
                return []
            if candidate.startswith(("10.", "172.16.", "172.17.", "172.18.", "172.19.", "172.20.", "172.21.", "172.22.", "172.23.", "172.24.", "172.25.", "172.26.", "172.27.", "172.28.", "172.29.", "172.30.", "172.31.", "192.168.")):
                return [candidate]
            return []

        if isinstance(value, float):
            return []

        if isinstance(value, dict):
            preferred_keys = (
                "ip", "ipAddress", "ip_address", "address",
                "last_ip", "lastIp", "remote_ip", "remoteIp",
                "tunnel_ip", "tunnelIp", "client_ip", "clientIp", "virtual_ip", "virtualIp",
                "assigned_ip", "assignedIp", "vpn_ip", "vpnIp",
                "fixed_ip", "fixedIp", "network", "networkInfo",
                "network_info", "access", "connection",
            )
            for key in preferred_keys:
                if key in value:
                    for ip in cls._extract_ip_values(value.get(key), depth=depth + 1):
                        if ip not in found:
                            found.append(ip)
            for key, nested in value.items():
                key_l = str(key).lower()
                if not any(token in key_l for token in ("ip", "address", "network", "client", "access", "vpn")):
                    continue
                for ip in cls._extract_ip_values(nested, depth=depth + 1):
                    if ip not in found:
                        found.append(ip)
            return found

        if isinstance(value, list):
            for item in value[:20]:
                for ip in cls._extract_ip_values(item, depth=depth + 1):
                    if ip not in found:
                        found.append(ip)
            return found

        return []

    @classmethod
    def _pick_ips(cls, row: dict[str, Any]) -> list[str]:
        """Return all useful IP values from a UniFi row."""
        return cls._extract_ip_values(row)

    @classmethod
    def _pick_ip(cls, row: dict[str, Any]) -> str | None:
        """Return the most likely client IP value from a UniFi row."""
        ips = cls._pick_ips(row)
        return ips[0] if ips else None

    @classmethod
    def _pick_mac(cls, row: dict[str, Any]) -> str | None:
        """Return the most likely client MAC value from a UniFi row."""
        for key in ("mac", "macAddress", "mac_address", "last_mac", "lastMac"):
            value = cls._str_or_none(row.get(key))
            if value:
                return value
        return None

    @classmethod
    def _pick_hostname(cls, row: dict[str, Any]) -> str | None:
        """Return the most likely hostname value from a UniFi row."""
        for key in ("hostname", "host", "hostName", "client_name", "clientName"):
            value = cls._str_or_none(row.get(key))
            if value:
                return value
        return None

    @classmethod
    def _pick_user(cls, row: dict[str, Any]) -> str:
        """Return a useful display name for a VPN client row."""
        for key in (
            "username",
            "user",
            "user_name",
            "userName",
            "display_name",
            "displayName",
            "name",
            "hostname",
            "host",
        ):
            value = cls._str_or_none(row.get(key))
            if value:
                return value
        return cls._pick_hostname(row) or cls._pick_ip(row) or "unknown"

    @classmethod
    def _normal_state(cls, row: dict[str, Any]) -> str:
        """Normalize VPN connection state."""
        if row.get("state") not in (None, ""):
            return str(row["state"]).upper()
        if row.get("status") not in (None, ""):
            return str(row["status"]).upper()
        if "connected" in row:
            return "CONNECTED" if cls._truthy(row.get("connected")) else "DISCONNECTED"
        if "is_connected" in row:
            return "CONNECTED" if cls._truthy(row.get("is_connected")) else "DISCONNECTED"
        if row.get("connectedAt") is not None or row.get("connected_at") is not None:
            return "CONNECTED"
        return "UNKNOWN"

    @classmethod
    def _is_explicitly_disconnected(cls, row: dict[str, Any]) -> bool:
        """Return True when a VPN row explicitly says disconnected."""
        state = str(row.get("state") or row.get("status") or "").lower()
        if state in {"disconnected", "offline", "down", "inactive", "idle"}:
            return True
        if "connected" in row and cls._falsy(row.get("connected")):
            return True
        if "is_connected" in row and cls._falsy(row.get("is_connected")):
            return True
        return False

    @classmethod
    def _is_remote_active(cls, row: dict[str, Any]) -> bool:
        """Return True for active or probably-active remote user VPN rows."""
        if cls._is_explicitly_disconnected(row):
            return False
        if cls._normal_state(row) == "CONNECTED":
            return True
        return True

    def _matches_manual_teleport_client(self, row: dict[str, Any]) -> bool:
        """Return True if a row matches a manually configured Teleport matcher."""
        if not self._teleport_matchers:
            return False

        values: list[str] = []
        for key in (
            "id", "_id", "name", "displayName", "hostname", "host",
            "client_name", "clientName", "ip", "ipAddress", "last_ip",
            "mac", "macAddress", "oui", "manufacturer", "network", "networkName",
        ):
            value = row.get(key)
            if value in (None, ""):
                continue
            values.append(str(value).lower())

        for ip in self._pick_ips(row):
            values.append(ip.lower())
        mac = self._pick_mac(row)
        if mac:
            values.append(re.sub(r"[^0-9a-f]", "", mac.lower()))

        haystack = " | ".join(values)
        return any(matcher in haystack for matcher in self._teleport_matchers)

    def _matches_teleport_range(self, ip: str) -> bool:
        """Return True if an IP matches the configured Teleport prefix or CIDR range."""
        value = (self.teleport_prefix or DEFAULT_TELEPORT_PREFIX).strip()
        if not ip or not value:
            return False

        if "/" in value:
            try:
                return ipaddress.ip_address(ip) in ipaddress.ip_network(value, strict=False)
            except ValueError:
                _LOGGER.debug("Invalid Teleport CIDR range %s; falling back to prefix matching", value)

        return ip.startswith(value)

    def _is_teleport(self, row: dict[str, Any], *, source: str = "integration") -> bool:
        """Return True for Teleport-looking client rows.

        UniFi exposes Teleport differently depending on Network version and API
        family. The official Integration API uses type/clientType TELEPORT, while
        older local endpoints often only expose the assigned Teleport IP range or
        a network/access object that mentions Teleport.
        """
        if self._is_explicitly_disconnected(row):
            return False

        if self._matches_manual_teleport_client(row):
            return True

        connection_type_values = (
            row.get("type"), row.get("clientType"), row.get("client_type"),
            row.get("vpnType"), row.get("vpn_type"), row.get("connectionType"),
            row.get("connection_type"), row.get("network_type"), row.get("networkType"),
        )
        connection_type = " ".join(str(value).upper() for value in connection_type_values if value not in (None, ""))
        if "TELEPORT" in connection_type:
            return True

        def _contains_teleport(value: Any, *, depth: int = 0) -> bool:
            if depth > 3 or value in (None, ""):
                return False
            if isinstance(value, str):
                return "teleport" in value.lower()
            if isinstance(value, dict):
                return any(_contains_teleport(v, depth=depth + 1) for v in value.values())
            if isinstance(value, list):
                return any(_contains_teleport(v, depth=depth + 1) for v in value[:20])
            return False

        for key in ("network", "networkName", "network_name", "access", "connection", "name", "displayName"):
            if key in row and _contains_teleport(row.get(key)):
                return True

        ips = self._pick_ips(row)
        if any(self._matches_teleport_range(ip) for ip in ips):
            return True

        return False

    def _looks_like_vpn_session(self, row: dict[str, Any]) -> bool:
        """Return True for active-looking non-Teleport VPN rows from probe endpoints."""
        if self._is_explicitly_disconnected(row):
            return False
        text = " ".join(
            str(row.get(key) or "")
            for key in ("type", "clientType", "vpnType", "vpn_type", "connectionType", "connection_type", "protocol", "name")
        ).lower()
        if any(token in text for token in ("vpn", "wireguard", "l2tp", "openvpn", "ipsec", "teleport")):
            return True
        if row.get("remote_ip") or row.get("remoteIp") or row.get("tunnel_ip") or row.get("tunnelIp"):
            return True
        return False

    def _client_debug_snapshot(self, rows: list[dict[str, Any]], *, limit: int = 12) -> list[dict[str, Any]]:
        """Return privacy-conscious client rows for debugging VPN detection."""
        snapshot: list[dict[str, Any]] = []
        interesting_keys = (
            "id", "_id", "name", "displayName", "hostname", "host",
            "type", "clientType", "vpnType", "connectionType",
            "network", "networkName", "ip", "ipAddress", "last_ip",
            "remote_ip", "remoteIp", "tunnel_ip", "tunnelIp", "virtualIp", "mac", "macAddress",
            "connected", "state", "status", "connectedAt", "last_seen", "_unm_source",
        )
        for row in rows[:limit]:
            slim = {key: row.get(key) for key in interesting_keys if key in row}
            slim["detected_ips"] = self._pick_ips(row)
            slim["matches_teleport_range"] = any(self._matches_teleport_range(ip) for ip in slim["detected_ips"])
            slim["matches_manual_teleport_client"] = self._matches_manual_teleport_client(row)
            slim["detected_as_teleport"] = self._is_teleport(row, source="debug")
            snapshot.append(slim)
        return snapshot

    @classmethod
    def _normalize_remote(cls, row: dict[str, Any]) -> dict[str, Any]:
        """Normalize a remote user VPN row."""
        user = cls._pick_user(row)
        ip = cls._pick_ip(row)
        connection_type = str(
            row.get("type")
            or row.get("connectionType")
            or row.get("connection_type")
            or row.get("vpnType")
            or row.get("vpn_type")
            or "L2TP"
        ).upper()
        state = cls._normal_state(row)
        if state == "UNKNOWN":
            state = "CONNECTED"
        return {
            "id": row.get("id") or row.get("_id"),
            "user": user,
            "name": user,
            "display_name": user,
            "hostname": cls._pick_hostname(row),
            "username": row.get("username") or row.get("user"),
            "ip": ip,
            "mac": cls._pick_mac(row),
            "type": connection_type,
            "connection_type": row.get("connection_type")
            or row.get("connectionType")
            or row.get("vpn_type")
            or row.get("vpnType")
            or row.get("protocol")
            or "L2TP",
            "vpn_type": row.get("vpn_type") or row.get("vpnType") or row.get("protocol") or "L2TP",
            "state": state,
            "status": state,
            "connected_at": row.get("connectedAt") or row.get("connected_at"),
            "source": "vpn_raw",
            "label": f"{user} [{connection_type}] • {ip or 'no-ip'}",
        }

    @classmethod
    def _normalize_teleport(cls, row: dict[str, Any], *, source: str) -> dict[str, Any]:
        """Normalize a Teleport client row."""
        user = cls._pick_user(row)
        ip = cls._pick_ip(row)
        return {
            "id": row.get("id") or row.get("_id"),
            "user": user,
            "name": user,
            "display_name": user,
            "hostname": cls._pick_hostname(row),
            "username": row.get("username") or row.get("user"),
            "ip": ip,
            "mac": cls._pick_mac(row),
            "type": "TELEPORT",
            "connection_type": "TELEPORT",
            "vpn_type": "TELEPORT",
            "state": "CONNECTED",
            "status": "CONNECTED",
            "connected_at": row.get("connectedAt")
            or row.get("connected_at")
            or row.get("last_seen")
            or row.get("lastSeen"),
            "source": source,
            "label": f"{user} [TELEPORT] • {ip or 'no-ip'}",
        }

    @staticmethod
    def _normalize_placeholder(n: int) -> dict[str, Any]:
        """Create a placeholder for counted but not individually listed L2TP clients."""
        label = f"L2TP client {n} [L2TP]"
        return {
            "id": None,
            "user": f"L2TP client {n}",
            "name": f"L2TP client {n}",
            "display_name": f"L2TP client {n}",
            "hostname": None,
            "username": None,
            "ip": None,
            "mac": None,
            "type": "L2TP",
            "connection_type": "L2TP",
            "vpn_type": "L2TP",
            "state": "CONNECTED",
            "status": "CONNECTED",
            "connected_at": None,
            "source": "vpn_summary_fallback",
            "label": label,
        }

    @staticmethod
    def _dedupe_vpn_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Sort and de-duplicate normalized VPN rows.

        Prefer richer API rows over local /stat/sta fallback rows when both
        describe the same session.
        """
        source_priority = {
            "vpn_raw": 0,
            "clients_integration_raw": 1,
            "clients_raw": 2,
            "vpn_summary_fallback": 3,
        }
        sorted_rows = sorted(
            rows,
            key=lambda row: (
                str(row.get("type") or ""),
                str(row.get("ip") or ""),
                str(row.get("name") or ""),
                source_priority.get(str(row.get("source") or ""), 9),
            ),
        )
        seen: set[tuple[str, str, str]] = set()
        result: list[dict[str, Any]] = []
        for row in sorted_rows:
            key = (
                str(row.get("type") or ""),
                str(row.get("ip") or ""),
                str(row.get("name") or ""),
            )
            if key in seen:
                continue
            seen.add(key)
            result.append(row)
        return result

    @classmethod
    def _vpn_summary(
        cls,
        vpn_raw: list[dict[str, Any]],
        vpn_health_raw: list[dict[str, Any]],
        vpn_http: int,
    ) -> dict[str, Any]:
        """Build a small VPN summary equivalent to the Bash jq pre-processing output."""
        vpn_health = vpn_health_raw[0] if vpn_health_raw else {}
        if vpn_raw:
            active_count = sum(1 for row in vpn_raw if cls._normal_state(row) == "CONNECTED")
            total_count = len(vpn_raw)
        else:
            active_count = int(vpn_health.get("remote_user_num_active") or 0)
            total_count = active_count + int(vpn_health.get("remote_user_num_inactive") or 0)

        return {
            "active_count": active_count,
            "total_count": total_count,
            "enabled": vpn_health.get("remote_user_enabled"),
            "site_to_site_enabled": vpn_health.get("site_to_site_enabled"),
            "rx_bytes": vpn_health.get("remote_user_rx_bytes") or 0,
            "tx_bytes": vpn_health.get("remote_user_tx_bytes") or 0,
            "source": "stat/remoteuservpn" if vpn_http == 200 else ("stat/health.vpn" if vpn_health else "none"),
            "endpoint_available": vpn_http == 200,
        }

    def _vpn_status(
        self,
        *,
        vpn_raw: list[dict[str, Any]],
        vpn_health_raw: list[dict[str, Any]],
        clients_raw: list[dict[str, Any]],
        clients_integration_raw: list[dict[str, Any]],
        client_inventory: dict[str, Any],
        vpn_probe: dict[str, Any],
        cached_summary: dict[str, Any],
        meta: dict[str, Any],
    ) -> dict[str, Any]:
        """Build the richer VPN/Teleport status formerly produced by get_vpn_status.sh.

        Uses the Integration API first and falls back to merged local client inventories.
        Some UniFi versions list Teleport clients as ordinary clients.
        """
        probe_rows = vpn_probe.get("rows") if isinstance(vpn_probe, dict) else []
        if not isinstance(probe_rows, list):
            probe_rows = []
        remote_users = [self._normalize_remote(row) for row in vpn_raw if self._is_remote_active(row)]
        teleport_users_integration = [
            self._normalize_teleport(row, source="clients_integration_raw")
            for row in clients_integration_raw
            if self._is_teleport(row, source="integration")
        ]
        teleport_users_legacy = [
            self._normalize_teleport(row, source="clients_raw")
            for row in clients_raw
            if self._is_teleport(row, source="legacy")
        ]
        teleport_users_probe = [
            self._normalize_teleport(row, source="vpn_probe")
            for row in probe_rows
            if isinstance(row, dict) and self._is_teleport(row, source="probe")
        ]
        remote_users_probe = [
            self._normalize_remote(row) | {"source": "vpn_probe"}
            for row in probe_rows
            if isinstance(row, dict) and not self._is_teleport(row, source="probe") and self._looks_like_vpn_session(row)
        ]
        teleport_users = self._dedupe_vpn_rows(teleport_users_integration + teleport_users_legacy + teleport_users_probe)
        remote_users = self._dedupe_vpn_rows(remote_users + remote_users_probe)

        reported_remote_active = max(int(cached_summary.get("active_count") or 0), len(vpn_raw))
        missing_remote_count = max(0, reported_remote_active - len(remote_users))
        remote_placeholders = [self._normalize_placeholder(n) for n in range(1, missing_remote_count + 1)]

        data = self._dedupe_vpn_rows(remote_users + remote_placeholders + teleport_users)
        remote_all = remote_users + remote_placeholders

        total_count_base = int(cached_summary.get("total_count") or 0)
        total_count = (total_count_base if total_count_base > 0 else reported_remote_active) + len(teleport_users)
        connected_users = ", ".join(str(row.get("label") or "") for row in data if row.get("label"))

        remote_count = len(remote_all)
        teleport_count = len(teleport_users)
        teleport_sources = sorted({str(row.get("source")) for row in teleport_users if row.get("source")})
        if remote_count > 0 and teleport_count > 0:
            source = "stat/remoteuservpn+" + "+".join(teleport_sources or ["clients"])
        elif teleport_count > 0:
            source = "+".join(teleport_sources or ["clients"])
        elif remote_count > 0:
            source = str(cached_summary.get("source") or "stat/remoteuservpn")
        else:
            source = str(cached_summary.get("source") or "none")

        summary = {
            "active_count": len(data),
            "total_count": total_count,
            "enabled": cached_summary.get("enabled"),
            "site_to_site_enabled": cached_summary.get("site_to_site_enabled"),
            "rx_bytes": cached_summary.get("rx_bytes") or 0,
            "tx_bytes": cached_summary.get("tx_bytes") or 0,
            "source": source,
            "endpoint_available": meta.get("vpn_http") == "200",
            "raw_count": len(vpn_raw),
            "remote_user_count": remote_count,
            "teleport_count": teleport_count,
            "teleport_integration_count": len(teleport_users_integration),
            "teleport_legacy_count": len(teleport_users_legacy),
            "teleport_probe_count": len(teleport_users_probe),
            "remote_probe_count": len(remote_users_probe),
            "clients_endpoint_available": meta.get("integration_clients_http") == "200" or meta.get("clients_http") == "200",
            "integration_clients_endpoint_available": meta.get("integration_clients_http") == "200",
            "legacy_clients_endpoint_available": meta.get("clients_http") == "200",
            "clients_raw_count": len(clients_raw),
            "clients_inventory_row_count": int(meta.get("client_inventory_row_count") or 0),
            "clients_inventory_teleport_range_match_count": int(meta.get("client_inventory_teleport_range_match_count") or 0),
            "clients_integration_raw_count": len(clients_integration_raw),
            "teleport_manual_matcher_count": len(self._teleport_matchers),
            "connected_users": connected_users,
        }

        return {
            "summary": summary,
            "data": data,
            "raw": data,
            "raw_remote": remote_all,
            "raw_teleport_candidates": teleport_users,
            "raw_teleport_candidates_integration": teleport_users_integration,
            "raw_teleport_candidates_legacy": teleport_users_legacy,
            "meta": {
                "host": meta.get("host"),
                "site": meta.get("site"),
                "ts": meta.get("ts"),
                "stale": meta.get("stale"),
                "vpn_http": meta.get("vpn_http"),
                "health_http": meta.get("health_http"),
                "clients_http": meta.get("clients_http"),
                "integration_sites_http": meta.get("integration_sites_http"),
                "integration_clients_http": meta.get("integration_clients_http"),
                "client_inventory_http": meta.get("client_inventory_http"),
                "client_inventory_row_count": meta.get("client_inventory_row_count"),
                "client_inventory_teleport_range_match_count": meta.get("client_inventory_teleport_range_match_count"),
                "integration_base_path": meta.get("integration_base_path"),
                "integration_auth_method": meta.get("integration_auth_method"),
                "integration_clients_pages": meta.get("integration_clients_pages"),
                "integration_clients_total": meta.get("integration_clients_total"),
                "integration_clients_row_count": meta.get("integration_clients_row_count"),
                "teleport_prefix": meta.get("teleport_prefix"),
                "teleport_client_matchers_configured": len(self._teleport_matchers),
            },
        }
