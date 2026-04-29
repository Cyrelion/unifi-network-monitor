"""Webhook support for UniFi Network Monitor."""
from __future__ import annotations

import json
import logging
import secrets
from collections.abc import Mapping
from typing import Any

from aiohttp import web

from homeassistant.components import webhook
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .const import (
    CONF_WEBHOOK_ENABLED,
    CONF_WEBHOOK_ID,
    CONF_WEBHOOK_LOCAL_ONLY,
    DEFAULT_WEBHOOK_ENABLED,
    DEFAULT_WEBHOOK_LOCAL_ONLY,
    DOMAIN,
)
from .coordinator import UniFiNetworkMonitorCoordinator

_LOGGER = logging.getLogger(__name__)


def get_webhook_id(entry: ConfigEntry) -> str:
    """Return the configured webhook id, generating one if needed."""
    value = entry.options.get(CONF_WEBHOOK_ID) or entry.data.get(CONF_WEBHOOK_ID)
    if isinstance(value, str) and value.strip():
        return value.strip()
    return secrets.token_urlsafe(32)


def get_webhook_enabled(entry: ConfigEntry) -> bool:
    """Return whether the WAN webhook endpoint is enabled."""
    return bool(entry.options.get(CONF_WEBHOOK_ENABLED, entry.data.get(CONF_WEBHOOK_ENABLED, DEFAULT_WEBHOOK_ENABLED)))


def get_webhook_local_only(entry: ConfigEntry) -> bool:
    """Return whether Home Assistant should only accept local webhook calls."""
    return bool(entry.options.get(CONF_WEBHOOK_LOCAL_ONLY, entry.data.get(CONF_WEBHOOK_LOCAL_ONLY, DEFAULT_WEBHOOK_LOCAL_ONLY)))


async def async_register_webhook(
    hass: HomeAssistant,
    entry: ConfigEntry,
    coordinator: UniFiNetworkMonitorCoordinator,
) -> None:
    """Register the UniFi Alarm Manager WAN webhook."""
    if not get_webhook_enabled(entry):
        coordinator.set_webhook_info(enabled=False, webhook_id="", local_only=False)
        return

    webhook_id = get_webhook_id(entry)
    if not entry.data.get(CONF_WEBHOOK_ID) and not entry.options.get(CONF_WEBHOOK_ID):
        hass.config_entries.async_update_entry(entry, data={**entry.data, CONF_WEBHOOK_ID: webhook_id})

    local_only = get_webhook_local_only(entry)

    async def _handle(*args: Any) -> web.Response:
        request = args[-1]
        return await async_handle_wan_webhook(coordinator, request)

    # Home Assistant versions differ slightly around the allowed_methods keyword.
    # Use it when available, fall back to the older call shape otherwise.
    try:
        webhook.async_register(
            hass,
            DOMAIN,
            "UniFi Network Monitor WAN",
            webhook_id,
            _handle,
            local_only=local_only,
            allowed_methods={"GET", "POST"},
        )
    except TypeError:
        webhook.async_register(
            hass,
            DOMAIN,
            "UniFi Network Monitor WAN",
            webhook_id,
            _handle,
            local_only=local_only,
        )

    coordinator.set_webhook_info(enabled=True, webhook_id=webhook_id, local_only=local_only)
    _LOGGER.debug("Registered UniFi Network Monitor webhook %s", webhook_id)


async def async_unregister_webhook(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Unregister the UniFi Alarm Manager WAN webhook."""
    webhook_id = entry.options.get(CONF_WEBHOOK_ID) or entry.data.get(CONF_WEBHOOK_ID)
    if webhook_id:
        webhook.async_unregister(hass, str(webhook_id))


async def async_handle_wan_webhook(
    coordinator: UniFiNetworkMonitorCoordinator,
    request: web.Request,
) -> web.Response:
    """Handle an incoming UniFi Alarm Manager WAN webhook request."""
    payload = await _read_payload(request)
    query = {key: value for key, value in request.query.items()}
    event = build_wan_event(payload, query, request.method)

    await coordinator.async_add_wan_event(event)

    coordinator.hass.bus.async_fire("unifi_network_monitor_wan_event", dict(event))
    coordinator.hass.bus.async_fire(
        "unifi_network_monitor_notification",
        {
            "title": "UDM WAN Event",
            "severity": event["severity"],
            "source": "unifi_network_monitor_webhook",
            "message": event["notify_text"],
            "push_tag": "udm_wan_event",
            "wan": event["wan"],
            "event": event["event"],
            "kind": event["kind"],
        },
    )

    return web.json_response(
        {
            "ok": True,
            "wan": event["wan"],
            "kind": event["kind"],
            "event": event["event"],
        }
    )


async def _read_payload(request: web.Request) -> dict[str, Any]:
    """Read JSON, form or text payload from a webhook request."""
    if request.method == "GET":
        return {}

    try:
        if request.content_type == "application/json":
            body = await request.json()
            return body if isinstance(body, dict) else {"data": body}

        if request.content_type in {"application/x-www-form-urlencoded", "multipart/form-data"}:
            data = await request.post()
            return {key: value for key, value in data.items()}

        text = await request.text()
    except Exception as err:  # noqa: BLE001
        _LOGGER.debug("Could not parse UniFi webhook payload: %s", err)
        return {}

    if not text:
        return {}

    try:
        body = json.loads(text)
    except json.JSONDecodeError:
        return {"raw_text": text[:1000]}
    return body if isinstance(body, dict) else {"data": body}


def build_wan_event(payload: Mapping[str, Any], query: Mapping[str, Any], method: str) -> dict[str, Any]:
    """Build a normalized WAN event row from UniFi Alarm Manager payloads."""
    parameters = payload.get("parameters") if isinstance(payload.get("parameters"), Mapping) else {}

    event_name = _first_value(payload, query, parameters, "name", "event", "eventName", "title") or "unknown"
    event_message = _first_value(payload, query, parameters, "message", "description", "text") or "n/a"
    wan_id_raw = _first_value(parameters, payload, query, "UNIFIwanId", "wanId", "wan", "wan_id") or ""
    wan_name_raw = _first_value(parameters, payload, query, "UNIFIwanName", "wanName", "wan_name") or ""
    wan_isp_raw = _first_value(parameters, payload, query, "UNIFIwanIsp", "wanIsp", "isp", "wan_isp") or ""

    wan = _normalize_wan(wan_id_raw or wan_name_raw)
    combined = f"{event_name} {event_message}".lower()

    # UniFi Alarm Manager uses several slightly different phrases depending on
    # UniFi Network/OS version and alert type. A temporary WAN disconnect is
    # still a WAN outage for the per-WAN counters, even when the overall
    # Internet connection stays usable through another WAN.
    up_tokens = (
        "internet restored",
        "restored",
        " is up",
        "back online",
        "recovered",
        "online again",
        "reconnected",
        "reconnection",
        "connection restored",
        "disconnection restored",
        "disconnection recovered",
    )
    down_tokens = (
        "temporary internet disconnection",
        "internet disconnection",
        "internet disconnected",
        "temporary disconnection",
        "wan disconnected",
        "wan disconnection",
        "internet down",
        " is down",
        "wan down",
        "went down",
        "offline",
        "unreachable",
    )

    is_up = any(token in combined for token in up_tokens)
    is_down = (not is_up) and any(token in combined for token in down_tokens)
    
    if is_down:
        kind = "down"
        severity = "warning"
        notify_text = f"{wan} ausgefallen {wan_name_raw} {wan_isp_raw}".strip()
    elif is_up:
        kind = "up"
        severity = "info"
        notify_text = f"{wan} wiederhergestellt {wan_name_raw} {wan_isp_raw}".strip()
    else:
        kind = "other"
        severity = "info"
        notify_text = f"UDM WAN Event {event_name} {wan} {wan_name_raw}".strip()

    now = dt_util.now()
    return {
        "ts": now.isoformat(),
        "date": now.date().isoformat(),
        "method": method,
        "wan": wan,
        "wan_id_raw": str(wan_id_raw),
        "wan_name": str(wan_name_raw or wan),
        "wan_isp": str(wan_isp_raw),
        "event": str(event_name),
        "message": str(event_message),
        "kind": kind,
        "severity": severity,
        "notify_text": notify_text,
        "parameters": _safe_mapping(parameters),
        "query": _safe_mapping(query),
    }

def _first_value(*args: Any) -> str | None:
    """Return the first non-empty string from mappings and keys.

    The function accepts mappings first and key names last. This slightly odd
    shape keeps call sites compact while supporting payload, query and nested
    parameters equally.
    """
    mappings: list[Mapping[str, Any]] = []
    keys: list[str] = []
    for arg in args:
        if isinstance(arg, Mapping):
            mappings.append(arg)
        else:
            keys.append(str(arg))

    for mapping in mappings:
        for key in keys:
            if key in mapping and mapping[key] not in (None, ""):
                return str(mapping[key])
    return None


def _normalize_wan(value: Any) -> str:
    """Normalize UniFi WAN identifiers."""
    text = str(value or "").strip().upper()
    if text in {"WAN", "WAN1", "1"}:
        return "WAN1"
    if text in {"WAN2", "2"}:
        return "WAN2"
    if "WAN2" in text:
        return "WAN2"
    if "WAN" in text:
        return "WAN1"
    return "UNKNOWN"


def _safe_mapping(value: Mapping[str, Any]) -> dict[str, Any]:
    """Return a small JSON-safe mapping for attributes/storage."""
    result: dict[str, Any] = {}
    for key, item in value.items():
        if isinstance(item, (str, int, float, bool)) or item is None:
            result[str(key)] = item if not isinstance(item, str) else item[:500]
        else:
            result[str(key)] = str(item)[:500]
    return result
