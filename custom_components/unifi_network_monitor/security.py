"""Strictly allowlisted WAN configuration data safe for entity-state exposure."""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

# Keys required for existing entities and internal routing logic.
# Never add entire upstream dictionaries or nested objects here.
WAN_CONFIG_PUBLIC_KEYS = frozenset({
    "purpose", "wan_networkgroup", "name", "wan_type", "wan_ip",
    "wan_gateway", "wan_load_balance_type", "wan_failover_priority",
    "wan_load_balance_weight",
})


def safe_wan_config(row: Mapping[str, Any]) -> dict[str, str | int | float | bool | None]:
    """Produce a fresh, flat allowlisted WAN row without credential-bearing fields."""
    return {
        key: value
        for key, value in row.items()
        if key in WAN_CONFIG_PUBLIC_KEYS
        and (value is None or type(value) in (str, int, float, bool))
    }


def safe_wan_config_rows(rows: Iterable[Any]) -> list[dict[str, str | int | float | bool | None]]:
    """Allowlist all WAN rows, dropping malformed and nested data."""
    return [safe_wan_config(row) for row in rows if isinstance(row, Mapping)]


# Exposure-facing fields are intentionally flat and scalar-only.
PUBLIC_META_KEYS = frozenset({
    "ts", "stale", "stale_reason", "stale_at", "last_success_at",
    "expected_wan_topology", "wanconf_http", "health_http", "vpn_http",
    "clients_http", "integration_sites_http", "integration_clients_http",
})
WAN_HEALTH_KEYS = frozenset({"subsystem", "status", "num_sta", "wan_ip"})
WAN_UPTIME_KEYS = frozenset({"availability", "latency_average", "uptime"})
VPN_ROW_KEYS = frozenset({
    "id", "user", "name", "display_name", "hostname", "username", "ip",
    "mac", "type", "connection_type", "vpn_type", "state", "status",
    "connected_at", "source", "label",
})
WAN_EVENT_KEYS = frozenset({
    "ts", "date", "method", "wan", "wan_name", "wan_isp", "event",
    "message", "kind", "severity", "notify_text",
})


def _flat_fields(row: Any, keys: frozenset[str]) -> dict[str, Any]:
    """Expose only named scalar fields; never pass through upstream objects."""
    if not isinstance(row, Mapping):
        return {}
    return {
        key: value for key, value in row.items()
        if key in keys and (value is None or type(value) in (str, int, float, bool))
    }


def safe_public_meta(meta: Any) -> dict[str, Any]:
    """Drop raw errors and arbitrary API metadata from state attributes."""
    return _flat_fields(meta, PUBLIC_META_KEYS)


def safe_wan_health(row: Any) -> dict[str, Any]:
    """Keep basic WAN status and known numerical uptime statistics."""
    result = _flat_fields(row, WAN_HEALTH_KEYS)
    if isinstance(row, Mapping) and isinstance(row.get("uptime_stats"), Mapping):
        result["uptime_stats"] = {
            group: _flat_fields(row["uptime_stats"].get(group), WAN_UPTIME_KEYS)
            for group in ("WAN", "WAN2")
            if isinstance(row["uptime_stats"].get(group), Mapping)
        }
    return result


def safe_wan_stats(row: Any) -> dict[str, Any]:
    """Retain only uptime/latency/availability telemetry."""
    return _flat_fields(row, WAN_UPTIME_KEYS)


def safe_vpn_rows(rows: Any) -> list[dict[str, Any]]:
    """Expose normalized session fields, not arbitrary upstream dictionaries."""
    if not isinstance(rows, list):
        return []
    return [_flat_fields(row, VPN_ROW_KEYS) for row in rows if isinstance(row, Mapping)]


def safe_wan_event(event: Any) -> dict[str, Any]:
    """Canonicalize untrusted event *values*, including persisted old history.

    An allowlisted key is not sufficient when its text is supplied by a webhook.
    """
    import re

    if not isinstance(event, Mapping):
        return {}
    wan = event.get("wan")
    wan = wan if type(wan) is str and wan in ("WAN1", "WAN2") else "UNKNOWN"
    kind = event.get("kind")
    kind = kind if type(kind) is str and kind in ("up", "down", "other") else "other"

    source_name = event.get("event")
    source_name = source_name.lower() if type(source_name) is str else ""
    if kind == "down":
        title, message, severity = "WAN Disconnected", "Connection lost", "warning"
    elif kind == "up":
        title, message, severity = "WAN Restored", "Connection restored", "info"
    elif "latency" in source_name:
        title, message, severity = "High Latency Detected", "High latency detected", "warning"
    else:
        title, message, severity = "WAN Event", "Network event received", "info"

    ts = event.get("ts")
    ts = ts if type(ts) is str and len(ts) <= 40 and re.fullmatch(r"[0-9T:.+Z-]+", ts) else ""
    date = event.get("date")
    date = date if type(date) is str and re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", date) else ""
    method = event.get("method")
    method = method if type(method) is str and method in ("GET", "POST") else ""
    return {
        "ts": ts, "date": date, "method": method, "wan": wan,
        "wan_name": wan, "wan_isp": "", "event": title,
        "message": message, "kind": kind, "severity": severity,
        "notify_text": f"{wan}: {message}",
    }


def safe_wan_events(events: Any) -> list[dict[str, Any]]:
    """Filter historical event rows as well as newly received ones."""
    if not isinstance(events, list):
        return []
    return [safe_wan_event(event) for event in events if isinstance(event, Mapping)]
