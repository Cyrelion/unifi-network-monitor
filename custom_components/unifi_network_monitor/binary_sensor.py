"""Binary sensor platform for UniFi Network Monitor."""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Callable

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity, BinarySensorEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DEFAULT_CACHE_MAX_AGE, DOMAIN
from .coordinator import UniFiNetworkMonitorCoordinator
from .entity import UniFiNetworkMonitorEntity, float_value, is_wan_online, wan_stats

StateFn = Callable[[dict[str, Any]], bool]
AttributesFn = Callable[[dict[str, Any]], dict[str, Any]]


@dataclass(frozen=True, kw_only=True)
class UniFiBinarySensorDescription(BinarySensorEntityDescription):
    """Binary sensor description."""

    value_fn: StateFn
    attrs_fn: AttributesFn | None = None


def _api_fresh(data: dict[str, Any]) -> bool:
    meta = data.get("meta", {})
    timestamp = meta.get("ts")
    if not timestamp or meta.get("stale"):
        return False
    return (time.time() - float(timestamp)) < DEFAULT_CACHE_MAX_AGE


def _wan_attrs(data: dict[str, Any], key: str) -> dict[str, Any]:
    stats = wan_stats(data, key)
    return {
        "availability": float_value(stats.get("availability")),
        "latency_avg": float_value(stats.get("latency_average")),
        "uptime": stats.get("uptime", 0),
        "raw": stats,
    }



def _internet_online_attrs(data: dict[str, Any]) -> dict[str, Any]:
    """Return aggregate Internet online attributes."""
    return {
        "wan1_online": is_wan_online(data, "WAN"),
        "wan2_online": is_wan_online(data, "WAN2"),
        "wan1": _wan_attrs(data, "WAN"),
        "wan2": _wan_attrs(data, "WAN2"),
        "meta": data.get("meta", {}),
    }

def vpn_status(data: dict[str, Any]) -> dict[str, Any]:
    """Return rich VPN status data."""
    status = data.get("vpn_status")
    if isinstance(status, dict):
        return status
    return {
        "summary": data.get("vpn_summary", {}),
        "data": [],
        "raw_remote": [],
        "raw_teleport_candidates": [],
        "raw_teleport_candidates_integration": [],
        "raw_teleport_candidates_legacy": [],
    }


def vpn_summary(data: dict[str, Any]) -> dict[str, Any]:
    """Return VPN status summary."""
    summary = vpn_status(data).get("summary")
    return summary if isinstance(summary, dict) else {}


def _labels(rows: Any) -> list[str]:
    """Return labels from normalized VPN rows."""
    if not isinstance(rows, list):
        return []
    return [row.get("label") for row in rows if isinstance(row, dict) and row.get("label")]


def _vpn_active_attrs(data: dict[str, Any]) -> dict[str, Any]:
    summary = vpn_summary(data)
    return {"connected_users": summary.get("connected_users", "")}


def _teleport_attrs(data: dict[str, Any]) -> dict[str, Any]:
    return {"users": _labels(vpn_status(data).get("raw_teleport_candidates", []))}


def _remote_user_attrs(data: dict[str, Any]) -> dict[str, Any]:
    return {"users": _labels(vpn_status(data).get("raw_remote", []))}


def traffic(data: dict[str, Any]) -> dict[str, Any]:
    """Return SNMP traffic data."""
    value = data.get("traffic")
    if isinstance(value, dict):
        return value
    return {
        "enabled": False,
        "ok": False,
        "status": "disabled",
        "values": {},
        "rates_bps": {},
        "rates_mbps": {},
        "raw_gb": {},
        "totals": {},
        "meta": {},
    }


def _traffic_meta(data: dict[str, Any]) -> dict[str, Any]:
    value = traffic(data)
    return {
        "enabled": value.get("enabled"),
        "status": value.get("status"),
        "meta": value.get("meta", {}),
    }


def _traffic_has_rates(data: dict[str, Any], *keys: str) -> bool:
    value = traffic(data)
    rates = value.get("rates_bps", {})
    if not isinstance(rates, dict):
        return False
    return value.get("ok") is True and all(key in rates for key in keys)


BINARY_SENSORS: tuple[UniFiBinarySensorDescription, ...] = (
    UniFiBinarySensorDescription(
        key="wan_api_fresh",
        name="WAN API Fresh",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        value_fn=_api_fresh,
        attrs_fn=lambda data: {"meta": data.get("meta", {})},
    ),

    UniFiBinarySensorDescription(
        key="internet_online",
        name="Internet Online",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        value_fn=lambda data: is_wan_online(data, "WAN") or is_wan_online(data, "WAN2"),
        attrs_fn=_internet_online_attrs,
    ),
    UniFiBinarySensorDescription(
        key="wan1_online",
        name="WAN1 Online",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        value_fn=lambda data: is_wan_online(data, "WAN"),
        attrs_fn=lambda data: _wan_attrs(data, "WAN"),
    ),
    UniFiBinarySensorDescription(
        key="wan2_online",
        name="WAN2 Online",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        value_fn=lambda data: is_wan_online(data, "WAN2"),
        attrs_fn=lambda data: _wan_attrs(data, "WAN2"),
    ),
    UniFiBinarySensorDescription(
        key="vpn_active",
        name="VPN Active",
        icon="mdi:vpn",
        value_fn=lambda data: int(vpn_summary(data).get("active_count") or 0) > 0,
        attrs_fn=_vpn_active_attrs,
    ),
    UniFiBinarySensorDescription(
        key="teleport_active",
        name="Teleport Active",
        icon="mdi:airplane",
        value_fn=lambda data: int(vpn_summary(data).get("teleport_count") or 0) > 0,
        attrs_fn=_teleport_attrs,
    ),
    UniFiBinarySensorDescription(
        key="remote_user_vpn_active",
        name="Remote User VPN Active",
        icon="mdi:account-network",
        value_fn=lambda data: int(vpn_summary(data).get("remote_user_count") or 0) > 0,
        attrs_fn=_remote_user_attrs,
    ),
    UniFiBinarySensorDescription(
        key="vpn_endpoint_available",
        name="VPN Endpoint Available",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        value_fn=lambda data: bool(vpn_summary(data).get("endpoint_available")),
    ),
    UniFiBinarySensorDescription(
        key="vpn_clients_endpoint_available",
        name="VPN Clients Endpoint Available",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        value_fn=lambda data: bool(vpn_summary(data).get("clients_endpoint_available")),
    ),
    UniFiBinarySensorDescription(
        key="snmp_traffic_healthy",
        name="SNMP Traffic Healthy",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        value_fn=lambda data: traffic(data).get("ok") is True,
        attrs_fn=_traffic_meta,
    ),
    UniFiBinarySensorDescription(
        key="snmp_traffic_updating",
        name="SNMP Traffic Updating",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        value_fn=lambda data: traffic(data).get("status") in {"ok", "partial"},
        attrs_fn=_traffic_meta,
    ),
    UniFiBinarySensorDescription(
        key="wan1_mbps_fresh",
        name="WAN1 Mbps Fresh",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        value_fn=lambda data: _traffic_has_rates(data, "wan1_rx", "wan1_tx"),
        attrs_fn=_traffic_meta,
    ),
    UniFiBinarySensorDescription(
        key="wan2_mbps_fresh",
        name="WAN2 Mbps Fresh",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        value_fn=lambda data: _traffic_has_rates(data, "wan2_rx", "wan2_tx"),
        attrs_fn=_traffic_meta,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up UniFi Network Monitor binary sensors."""
    coordinator: UniFiNetworkMonitorCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        UniFiNetworkMonitorBinarySensor(coordinator, entry, description)
        for description in BINARY_SENSORS
    )


class UniFiNetworkMonitorBinarySensor(UniFiNetworkMonitorEntity, BinarySensorEntity):
    """UniFi Network Monitor binary sensor."""

    entity_description: UniFiBinarySensorDescription

    def __init__(
        self,
        coordinator: UniFiNetworkMonitorCoordinator,
        entry: ConfigEntry,
        description: UniFiBinarySensorDescription,
    ) -> None:
        """Initialize binary sensor."""
        super().__init__(coordinator, entry, description.key)
        self.entity_description = description
        self._attr_name = description.name

    @property
    def is_on(self) -> bool:
        """Return binary sensor state."""
        return self.entity_description.value_fn(self.coordinator.data or {})

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return extra attributes."""
        if self.entity_description.attrs_fn is None:
            return None
        return self.entity_description.attrs_fn(self.coordinator.data or {})
