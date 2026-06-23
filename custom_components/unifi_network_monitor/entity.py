"""Entity helpers for UniFi Network Monitor."""
from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    DOMAIN,
    NAME,
    TOPOLOGY_DUAL_WAN,
    TOPOLOGY_WAN1_ONLY,
    TOPOLOGY_WAN2_ONLY,
)
from .coordinator import UniFiNetworkMonitorCoordinator


def wan_config(data: dict[str, Any], networkgroup: str) -> dict[str, Any] | None:
    """Return WAN config row by network group."""
    for row in data.get("wan_config", []):
        if row.get("wan_networkgroup") == networkgroup:
            return row
    return None


def wan_health(data: dict[str, Any]) -> dict[str, Any]:
    """Return the first WAN health row."""
    rows = data.get("wan_health_raw", [])
    return rows[0] if rows else {}


def wan_stats(data: dict[str, Any], key: str) -> dict[str, Any]:
    """Return uptime_stats for WAN or WAN2."""
    health = wan_health(data)
    uptime_stats = health.get("uptime_stats") or {}
    row = uptime_stats.get(key) or {}
    return row if isinstance(row, dict) else {}


def float_value(value: Any, default: float = 0.0) -> float:
    """Safely convert to float."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def int_value(value: Any, default: int = 0) -> int:
    """Safely convert to int."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return default




def configured_topology(data: dict[str, Any]) -> str:
    """Return configured expected WAN topology."""
    return str((data.get("meta") or {}).get("expected_wan_topology") or "auto").lower()


def auto_expected_wans(data: dict[str, Any]) -> set[str]:
    """Best-effort detection of expected WAN uplinks from available data."""
    expected: set[str] = set()

    wan1_cfg = wan_config(data, "WAN") or {}
    wan2_cfg = wan_config(data, "WAN2") or {}
    wan1_stats = wan_stats(data, "WAN")
    wan2_stats = wan_stats(data, "WAN2")

    if wan1_cfg or wan1_stats:
        expected.add("WAN")
    if wan2_cfg or wan2_stats:
        expected.add("WAN2")

    return expected


def expected_wans(data: dict[str, Any]) -> set[str]:
    """Return WAN uplinks that should participate in health evaluation."""
    topology = configured_topology(data)

    if topology == TOPOLOGY_WAN1_ONLY:
        return {"WAN"}
    if topology == TOPOLOGY_WAN2_ONLY:
        return {"WAN2"}
    if topology == TOPOLOGY_DUAL_WAN:
        return {"WAN", "WAN2"}

    return auto_expected_wans(data)

def is_wan_online(data: dict[str, Any], key: str) -> bool:
    """Return whether a WAN link looks online according to UniFi uptime stats."""
    stats = wan_stats(data, key)
    availability = float_value(stats.get("availability"))
    uptime = int_value(stats.get("uptime"))
    latency = float_value(stats.get("latency_average"))
    return availability > 0 or uptime > 0 or latency > 0


def latency(data: dict[str, Any], key: str, fallback: float) -> float:
    """Return latency average or fallback."""
    value = float_value(wan_stats(data, key).get("latency_average"))
    return round(value if value > 0 else fallback, 1)


def stability_score(data: dict[str, Any], key: str, expected_latency: float) -> int:
    """Calculate the same simple stability score as the YAML package."""
    stats = wan_stats(data, key)
    availability = float_value(stats.get("availability"))
    current_latency = float_value(stats.get("latency_average"))
    online = is_wan_online(data, key)

    availability_score = availability * 0.60
    if expected_latency > 0:
        deviation = current_latency / expected_latency if current_latency > 0 else 1
        latency_score_raw = 100 - ((deviation - 1) * 100)
    else:
        latency_score_raw = 100

    latency_score = min(max(latency_score_raw, 0), 100)
    online_score = 15 if online else 0
    total = availability_score + (latency_score * 0.25) + online_score
    return round(min(max(total, 0), 100))


def routing_mode(data: dict[str, Any]) -> str:
    """Calculate internet routing mode from WAN health and WAN config."""
    meta = data.get("meta", {})
    if meta.get("stale"):
        return "STALE"

    health = wan_health(data)
    data_valid = bool(data.get("wan_config"))
    health_valid = bool((health.get("uptime_stats") or {}).get("WAN") or (health.get("uptime_stats") or {}).get("WAN2"))
    if not data_valid or not health_valid:
        return "UNKNOWN"

    wan1_online = is_wan_online(data, "WAN")
    wan2_online = is_wan_online(data, "WAN2")
    wan1 = wan_config(data, "WAN") or {}
    wan2 = wan_config(data, "WAN2") or {}

    if not wan1_online and not wan2_online:
        return "OFFLINE"
    if wan1_online and wan2_online:
        if wan1.get("wan_load_balance_type") == "weighted" and wan2.get("wan_load_balance_type") == "weighted":
            return "BALANCED"
        wan1_prio = int_value(wan1.get("wan_failover_priority"), 999)
        wan2_prio = int_value(wan2.get("wan_failover_priority"), 999)
        return "WAN1" if wan1_prio < wan2_prio else "WAN2"
    if wan1_online:
        return "WAN1"
    if wan2_online:
        return "WAN2"
    return "UNKNOWN"


class UniFiNetworkMonitorEntity(CoordinatorEntity[UniFiNetworkMonitorCoordinator]):
    """Base entity for UniFi Network Monitor."""

    _attr_has_entity_name = True

    @property
    def available(self) -> bool:
        """Keep entities available as long as we have any cached payload.

        The DataUpdateCoordinator marks all entities unavailable whenever the
        latest refresh fails. For this monitor short UniFi API/SNMP hiccups are
        expected, and the old YAML/Bash package continued to expose the last
        cache values. Once the first payload exists we keep entities available
        and expose freshness via the meta.stale attributes instead.
        """
        return self.coordinator.data is not None

    def __init__(self, coordinator: UniFiNetworkMonitorCoordinator, entry: ConfigEntry, key: str) -> None:
        """Initialize base entity."""
        super().__init__(coordinator)
        self.entry = entry
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=NAME,
            manufacturer="Ubiquiti",
            model="UniFi OS Gateway",
            configuration_url=f"https://{coordinator.api.host}",
        )
