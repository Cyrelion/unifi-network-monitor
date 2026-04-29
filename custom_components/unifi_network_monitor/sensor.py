"""Sensor platform for UniFi Network Monitor."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorEntityDescription, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import UniFiNetworkMonitorCoordinator
from .entity import (
    UniFiNetworkMonitorEntity,
    float_value,
    is_wan_online,
    latency,
    routing_mode,
    stability_score,
    wan_config,
    wan_health,
    wan_stats,
)

StateFn = Callable[[dict[str, Any]], Any]
AttributesFn = Callable[[dict[str, Any]], dict[str, Any]]


@dataclass(frozen=True, kw_only=True)
class UniFiSensorDescription(SensorEntityDescription):
    """Sensor description."""

    value_fn: StateFn
    attrs_fn: AttributesFn | None = None


def _wan_config_attrs(data: dict[str, Any], group: str) -> dict[str, Any]:
    row = wan_config(data, group) or {}
    return {
        "name": row.get("name", "N/A"),
        "type": row.get("wan_type", "N/A"),
        "ip": row.get("wan_ip", "N/A"),
        "gateway": row.get("wan_gateway", "N/A"),
        "load_balance_type": row.get("wan_load_balance_type", "N/A"),
        "failover_priority": row.get("wan_failover_priority", "N/A"),
        "weight": row.get("wan_load_balance_weight", "N/A"),
        "raw": row,
    }


def _wan_online_attrs(data: dict[str, Any], key: str) -> dict[str, Any]:
    stats = wan_stats(data, key)
    return {
        "availability": float_value(stats.get("availability")),
        "latency_avg": float_value(stats.get("latency_average")),
        "uptime": stats.get("uptime", 0),
        "online": is_wan_online(data, key),
    }


def _routing_attrs(data: dict[str, Any]) -> dict[str, Any]:
    wan1 = wan_config(data, "WAN") or {}
    wan2 = wan_config(data, "WAN2") or {}
    return {
        "wan1_name": wan1.get("name", "WAN1"),
        "wan2_name": wan2.get("name", "WAN2"),
        "wan1_online": is_wan_online(data, "WAN"),
        "wan2_online": is_wan_online(data, "WAN2"),
        "wan1_availability": float_value(wan_stats(data, "WAN").get("availability")),
        "wan2_availability": float_value(wan_stats(data, "WAN2").get("availability")),
        "wan1_latency": float_value(wan_stats(data, "WAN").get("latency_average")),
        "wan2_latency": float_value(wan_stats(data, "WAN2").get("latency_average")),
        "meta": data.get("meta", {}),
    }



def _internet_health_state(data: dict[str, Any]) -> str:
    """Return aggregated Internet health state from WAN1/WAN2."""
    wan1_online = is_wan_online(data, "WAN")
    wan2_online = is_wan_online(data, "WAN2")
    if wan1_online and wan2_online:
        return "Online"
    if wan1_online or wan2_online:
        return "Degraded"
    return "Offline"


def _internet_stability_score(data: dict[str, Any]) -> float:
    """Return aggregated stability score for all currently online WAN links."""
    scores: list[float] = []
    if is_wan_online(data, "WAN"):
        scores.append(float(stability_score(data, "WAN", 12)))
    if is_wan_online(data, "WAN2"):
        scores.append(float(stability_score(data, "WAN2", 30)))
    if not scores:
        return 0
    return round(sum(scores) / len(scores), 1)


def _internet_stability_state(data: dict[str, Any]) -> str:
    """Return human readable aggregated stability state."""
    score = _internet_stability_score(data)
    if score >= 90:
        return "Excellent"
    if score >= 75:
        return "Good"
    if score >= 50:
        return "Degraded"
    return "Critical"


def _internet_summary_attrs(data: dict[str, Any]) -> dict[str, Any]:
    """Return shared Internet summary attributes."""
    return {
        "wan1_online": is_wan_online(data, "WAN"),
        "wan2_online": is_wan_online(data, "WAN2"),
        "wan1_stability_score": stability_score(data, "WAN", 12),
        "wan2_stability_score": stability_score(data, "WAN2", 30),
        "routing_mode": routing_mode(data),
        "meta": data.get("meta", {}),
    }

def vpn_status(data: dict[str, Any]) -> dict[str, Any]:
    """Return rich VPN status data."""
    status = data.get("vpn_status")
    if isinstance(status, dict):
        return status
    summary = data.get("vpn_summary") if isinstance(data.get("vpn_summary"), dict) else {}
    return {
        "summary": summary,
        "data": [],
        "raw": [],
        "raw_remote": [],
        "raw_teleport_candidates": [],
        "raw_teleport_candidates_integration": [],
        "raw_teleport_candidates_legacy": [],
        "meta": data.get("meta", {}),
    }


def vpn_summary(data: dict[str, Any]) -> dict[str, Any]:
    """Return VPN status summary."""
    summary = vpn_status(data).get("summary")
    return summary if isinstance(summary, dict) else {}


def _vpn_raw_attrs(data: dict[str, Any]) -> dict[str, Any]:
    status = vpn_status(data)
    return {
        "summary": status.get("summary", {}),
        "data": status.get("data", []),
        "raw": status.get("raw", []),
        "raw_remote": status.get("raw_remote", []),
        "raw_teleport_candidates": status.get("raw_teleport_candidates", []),
        "raw_teleport_candidates_integration": status.get("raw_teleport_candidates_integration", []),
        "raw_teleport_candidates_legacy": status.get("raw_teleport_candidates_legacy", []),
        "meta": status.get("meta", {}),
    }


def _vpn_connected_users_attrs(data: dict[str, Any]) -> dict[str, Any]:
    status = vpn_status(data)
    rows = status.get("data", [])
    if not isinstance(rows, list):
        rows = []
    remote_rows = status.get("raw_remote", [])
    if not isinstance(remote_rows, list):
        remote_rows = []
    teleport_rows = status.get("raw_teleport_candidates", [])
    if not isinstance(teleport_rows, list):
        teleport_rows = []
    summary = vpn_summary(data)
    return {
        "full_text": summary.get("connected_users", ""),
        "users": [row.get("label") for row in rows if isinstance(row, dict) and row.get("label")],
        "raw_data": rows,
        "remote_users": remote_rows,
        "teleport_users": teleport_rows,
    }


def _vpn_endpoint_attrs(data: dict[str, Any]) -> dict[str, Any]:
    meta = vpn_status(data).get("meta", {})
    if not isinstance(meta, dict):
        meta = {}
    return {
        "health_http": meta.get("health_http", "unknown"),
        "clients_http": meta.get("clients_http", "unknown"),
        "integration_clients_http": meta.get("integration_clients_http", "unknown"),
        "stale": meta.get("stale"),
        "teleport_prefix": meta.get("teleport_prefix"),
    }


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


def _traffic_part(data: dict[str, Any], part: str) -> dict[str, Any]:
    value = traffic(data).get(part, {})
    return value if isinstance(value, dict) else {}


def _traffic_value(data: dict[str, Any], part: str, key: str, default: Any = None) -> Any:
    return _traffic_part(data, part).get(key, default)


def _traffic_attrs(data: dict[str, Any]) -> dict[str, Any]:
    value = traffic(data)
    return {
        "enabled": value.get("enabled"),
        "ok": value.get("ok"),
        "values": value.get("values", {}),
        "rates_bps": value.get("rates_bps", {}),
        "rates_mbps": value.get("rates_mbps", {}),
        "raw_gb": value.get("raw_gb", {}),
        "totals": value.get("totals", {}),
        "meta": value.get("meta", {}),
    }


def _traffic_metric_attrs(data: dict[str, Any], key: str) -> dict[str, Any]:
    value = traffic(data)
    return {
        "counter_octets": _traffic_value(data, "values", key),
        "rate_bps": _traffic_value(data, "rates_bps", key),
        "rate_mbps": _traffic_value(data, "rates_mbps", key),
        "raw_gb": _traffic_value(data, "raw_gb", key),
        "status": value.get("status"),
        "meta": value.get("meta", {}),
    }


def _traffic_total_attrs(data: dict[str, Any], *keys: str) -> dict[str, Any]:
    value = traffic(data)
    totals = _traffic_part(data, "totals")
    attrs: dict[str, Any] = {
        "status": value.get("status"),
        "totals": totals,
        "meta": value.get("meta", {}),
    }
    if keys:
        attrs["sources"] = {key: totals.get(key) for key in keys}
    return attrs


def wan_events(data: dict[str, Any]) -> dict[str, Any]:
    """Return local WAN webhook event history data."""
    value = data.get("wan_event_history")
    if isinstance(value, dict):
        return value
    return {
        "events": [],
        "count": 0,
        "last_event": {},
        "counters": {},
        "webhook": {},
        "meta": {},
    }


def _wan_events_attrs(data: dict[str, Any]) -> dict[str, Any]:
    value = wan_events(data)
    return {
        "events": value.get("events", []),
        "last_event": value.get("last_event", {}),
        "counters": value.get("counters", {}),
        "webhook": value.get("webhook", {}),
        "meta": value.get("meta", {}),
    }


def _wan_last_event_attrs(data: dict[str, Any]) -> dict[str, Any]:
    event = wan_events(data).get("last_event")
    if not isinstance(event, dict):
        event = {}
    return {
        "wan": event.get("wan", "UNKNOWN"),
        "wan_name": event.get("wan_name", "UNKNOWN"),
        "wan_isp": event.get("wan_isp", ""),
        "message": event.get("message", ""),
        "time": event.get("ts", ""),
        "kind": event.get("kind", "none"),
        "severity": event.get("severity", "info"),
        "notify_text": event.get("notify_text", ""),
        "raw": event,
    }


def _webhook_attrs(data: dict[str, Any]) -> dict[str, Any]:
    info = wan_events(data).get("webhook")
    if not isinstance(info, dict):
        info = {}
    webhook_id = str(info.get("webhook_id") or "")
    return {
        "enabled": bool(info.get("enabled")),
        "webhook_id": webhook_id,
        "local_only": bool(info.get("local_only")),
        "path": f"/api/webhook/{webhook_id}" if webhook_id else "",
        "example_url": f"http://homeassistant.local:8123/api/webhook/{webhook_id}" if webhook_id else "",
    }


def _event_counter(data: dict[str, Any], key: str) -> int:
    counters = wan_events(data).get("counters")
    if not isinstance(counters, dict):
        return 0
    try:
        return int(counters.get(key) or 0)
    except (TypeError, ValueError):
        return 0


SENSORS: tuple[UniFiSensorDescription, ...] = (
    UniFiSensorDescription(
        key="wan_health_raw",
        name="WAN Health Raw",
        icon="mdi:api",
        value_fn=lambda data: wan_health(data).get("status", "unknown"),
        attrs_fn=lambda data: dict(wan_health(data)),
    ),
    UniFiSensorDescription(
        key="wan_config_raw",
        name="WAN Config Raw",
        icon="mdi:api",
        value_fn=lambda data: f"{len(data.get('wan_config', []))} WANs",
        attrs_fn=lambda data: {"data": data.get("wan_config", []), "meta": data.get("meta", {})},
    ),
    UniFiSensorDescription(
        key="wan1_configuration",
        name="WAN1 Configuration",
        icon="mdi:ethernet",
        value_fn=lambda data: (wan_config(data, "WAN") or {}).get("name", "Not configured"),
        attrs_fn=lambda data: _wan_config_attrs(data, "WAN"),
    ),
    UniFiSensorDescription(
        key="wan2_configuration",
        name="WAN2 Configuration",
        icon="mdi:satellite-uplink",
        value_fn=lambda data: (wan_config(data, "WAN2") or {}).get("name", "Not configured"),
        attrs_fn=lambda data: _wan_config_attrs(data, "WAN2"),
    ),
    UniFiSensorDescription(
        key="wan1_latency_raw",
        name="WAN1 Latency Raw",
        icon="mdi:timer-outline",
        native_unit_of_measurement=UnitOfTime.MILLISECONDS,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: latency(data, "WAN", 12),
        attrs_fn=lambda data: _wan_online_attrs(data, "WAN"),
    ),
    UniFiSensorDescription(
        key="wan2_latency_raw",
        name="WAN2 Latency Raw",
        icon="mdi:timer-outline",
        native_unit_of_measurement=UnitOfTime.MILLISECONDS,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: latency(data, "WAN2", 30),
        attrs_fn=lambda data: _wan_online_attrs(data, "WAN2"),
    ),
    UniFiSensorDescription(
        key="wan1_expected_latency",
        name="WAN1 Expected Latency",
        icon="mdi:timer-cog-outline",
        native_unit_of_measurement=UnitOfTime.MILLISECONDS,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: 12,
    ),
    UniFiSensorDescription(
        key="wan2_expected_latency",
        name="WAN2 Expected Latency",
        icon="mdi:timer-cog-outline",
        native_unit_of_measurement=UnitOfTime.MILLISECONDS,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: 30,
    ),
    UniFiSensorDescription(
        key="wan1_stability_score",
        name="WAN1 Stability Score",
        icon="mdi:shield-check",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: stability_score(data, "WAN", 12),
        attrs_fn=lambda data: _wan_online_attrs(data, "WAN"),
    ),
    UniFiSensorDescription(
        key="wan2_stability_score",
        name="WAN2 Stability Score",
        icon="mdi:shield-check",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: stability_score(data, "WAN2", 30),
        attrs_fn=lambda data: _wan_online_attrs(data, "WAN2"),
    ),

    UniFiSensorDescription(
        key="internet_health_state",
        name="Internet Health State",
        icon="mdi:web-check",
        value_fn=_internet_health_state,
        attrs_fn=_internet_summary_attrs,
    ),
    UniFiSensorDescription(
        key="internet_stability_score",
        name="Internet Stability Score",
        icon="mdi:shield-check",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=_internet_stability_score,
        attrs_fn=_internet_summary_attrs,
    ),
    UniFiSensorDescription(
        key="internet_stability_state",
        name="Internet Stability State",
        icon="mdi:shield-star",
        value_fn=_internet_stability_state,
        attrs_fn=_internet_summary_attrs,
    ),
    UniFiSensorDescription(
        key="internet_routing_mode",
        name="Internet Routing Mode",
        icon="mdi:wan",
        value_fn=routing_mode,
        attrs_fn=_routing_attrs,
    ),
    UniFiSensorDescription(
        key="vpn_status_raw",
        name="VPN Status Raw",
        icon="mdi:vpn",
        native_unit_of_measurement="clients",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: int(vpn_summary(data).get("active_count") or 0),
        attrs_fn=_vpn_raw_attrs,
    ),
    UniFiSensorDescription(
        key="vpn_active_count",
        name="VPN Active Count",
        icon="mdi:vpn",
        native_unit_of_measurement="clients",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: int(vpn_summary(data).get("active_count") or 0),
    ),
    UniFiSensorDescription(
        key="vpn_total_count",
        name="VPN Total Count",
        icon="mdi:counter",
        native_unit_of_measurement="clients",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: int(vpn_summary(data).get("total_count") or 0),
    ),
    UniFiSensorDescription(
        key="vpn_remote_user_count",
        name="VPN Remote User Count",
        icon="mdi:account-network",
        native_unit_of_measurement="clients",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: int(vpn_summary(data).get("remote_user_count") or 0),
    ),
    UniFiSensorDescription(
        key="vpn_teleport_count",
        name="VPN Teleport Count",
        icon="mdi:airplane",
        native_unit_of_measurement="clients",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: int(vpn_summary(data).get("teleport_count") or 0),
    ),
    UniFiSensorDescription(
        key="vpn_source",
        name="VPN Source",
        icon="mdi:database-search",
        value_fn=lambda data: vpn_summary(data).get("source", "none"),
    ),
    UniFiSensorDescription(
        key="vpn_connected_users",
        name="VPN Connected Users",
        icon="mdi:account-group",
        value_fn=lambda data: vpn_summary(data).get("connected_users") or "none",
        attrs_fn=_vpn_connected_users_attrs,
    ),
    UniFiSensorDescription(
        key="vpn_endpoint_http",
        name="VPN Endpoint HTTP",
        icon="mdi:web",
        value_fn=lambda data: vpn_status(data).get("meta", {}).get("vpn_http", "unknown"),
        attrs_fn=_vpn_endpoint_attrs,
    ),
    UniFiSensorDescription(
        key="snmp_traffic_status",
        name="SNMP Traffic Status",
        icon="mdi:counter",
        value_fn=lambda data: traffic(data).get("status", "disabled"),
        attrs_fn=_traffic_attrs,
    ),
    UniFiSensorDescription(
        key="wan1_rx_octets",
        name="WAN1 RX Octets",
        icon="mdi:download-network",
        native_unit_of_measurement="B",
        state_class=SensorStateClass.TOTAL_INCREASING,
        value_fn=lambda data: _traffic_value(data, "values", "wan1_rx"),
        attrs_fn=lambda data: _traffic_metric_attrs(data, "wan1_rx"),
    ),
    UniFiSensorDescription(
        key="wan1_tx_octets",
        name="WAN1 TX Octets",
        icon="mdi:upload-network",
        native_unit_of_measurement="B",
        state_class=SensorStateClass.TOTAL_INCREASING,
        value_fn=lambda data: _traffic_value(data, "values", "wan1_tx"),
        attrs_fn=lambda data: _traffic_metric_attrs(data, "wan1_tx"),
    ),
    UniFiSensorDescription(
        key="wan2_rx_octets",
        name="WAN2 RX Octets",
        icon="mdi:download-network",
        native_unit_of_measurement="B",
        state_class=SensorStateClass.TOTAL_INCREASING,
        value_fn=lambda data: _traffic_value(data, "values", "wan2_rx"),
        attrs_fn=lambda data: _traffic_metric_attrs(data, "wan2_rx"),
    ),
    UniFiSensorDescription(
        key="wan2_tx_octets",
        name="WAN2 TX Octets",
        icon="mdi:upload-network",
        native_unit_of_measurement="B",
        state_class=SensorStateClass.TOTAL_INCREASING,
        value_fn=lambda data: _traffic_value(data, "values", "wan2_tx"),
        attrs_fn=lambda data: _traffic_metric_attrs(data, "wan2_tx"),
    ),
    UniFiSensorDescription(
        key="wan1_rx_rate_bps",
        name="WAN1 RX Rate Bps",
        icon="mdi:download-network",
        native_unit_of_measurement="B/s",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: _traffic_value(data, "rates_bps", "wan1_rx", 0),
        attrs_fn=lambda data: _traffic_metric_attrs(data, "wan1_rx"),
    ),
    UniFiSensorDescription(
        key="wan1_tx_rate_bps",
        name="WAN1 TX Rate Bps",
        icon="mdi:upload-network",
        native_unit_of_measurement="B/s",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: _traffic_value(data, "rates_bps", "wan1_tx", 0),
        attrs_fn=lambda data: _traffic_metric_attrs(data, "wan1_tx"),
    ),
    UniFiSensorDescription(
        key="wan2_rx_rate_bps",
        name="WAN2 RX Rate Bps",
        icon="mdi:download-network",
        native_unit_of_measurement="B/s",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: _traffic_value(data, "rates_bps", "wan2_rx", 0),
        attrs_fn=lambda data: _traffic_metric_attrs(data, "wan2_rx"),
    ),
    UniFiSensorDescription(
        key="wan2_tx_rate_bps",
        name="WAN2 TX Rate Bps",
        icon="mdi:upload-network",
        native_unit_of_measurement="B/s",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: _traffic_value(data, "rates_bps", "wan2_tx", 0),
        attrs_fn=lambda data: _traffic_metric_attrs(data, "wan2_tx"),
    ),
    UniFiSensorDescription(
        key="wan1_rx_rate",
        name="WAN1 RX Rate",
        icon="mdi:download-network",
        native_unit_of_measurement="Mbit/s",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: _traffic_value(data, "rates_mbps", "wan1_rx", 0),
        attrs_fn=lambda data: _traffic_metric_attrs(data, "wan1_rx"),
    ),
    UniFiSensorDescription(
        key="wan1_tx_rate",
        name="WAN1 TX Rate",
        icon="mdi:upload-network",
        native_unit_of_measurement="Mbit/s",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: _traffic_value(data, "rates_mbps", "wan1_tx", 0),
        attrs_fn=lambda data: _traffic_metric_attrs(data, "wan1_tx"),
    ),
    UniFiSensorDescription(
        key="wan2_rx_rate",
        name="WAN2 RX Rate",
        icon="mdi:download-network",
        native_unit_of_measurement="Mbit/s",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: _traffic_value(data, "rates_mbps", "wan2_rx", 0),
        attrs_fn=lambda data: _traffic_metric_attrs(data, "wan2_rx"),
    ),
    UniFiSensorDescription(
        key="wan2_tx_rate",
        name="WAN2 TX Rate",
        icon="mdi:upload-network",
        native_unit_of_measurement="Mbit/s",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: _traffic_value(data, "rates_mbps", "wan2_tx", 0),
        attrs_fn=lambda data: _traffic_metric_attrs(data, "wan2_tx"),
    ),
    UniFiSensorDescription(
        key="wan1_total_mbps",
        name="WAN1 Total Mbps",
        icon="mdi:speedometer",
        native_unit_of_measurement="Mbit/s",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: _traffic_value(data, "totals", "wan1_total_mbps", 0),
        attrs_fn=_traffic_attrs,
    ),
    UniFiSensorDescription(
        key="wan2_total_mbps",
        name="WAN2 Total Mbps",
        icon="mdi:speedometer",
        native_unit_of_measurement="Mbit/s",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: _traffic_value(data, "totals", "wan2_total_mbps", 0),
        attrs_fn=_traffic_attrs,
    ),
    UniFiSensorDescription(
        key="internet_total_rx_rate",
        name="Internet Total RX Rate",
        icon="mdi:download-network",
        native_unit_of_measurement="Mbit/s",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: _traffic_value(data, "totals", "internet_rx_mbps", 0),
        attrs_fn=_traffic_attrs,
    ),
    UniFiSensorDescription(
        key="internet_total_tx_rate",
        name="Internet Total TX Rate",
        icon="mdi:upload-network",
        native_unit_of_measurement="Mbit/s",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: _traffic_value(data, "totals", "internet_tx_mbps", 0),
        attrs_fn=_traffic_attrs,
    ),
    UniFiSensorDescription(
        key="internet_total_mbps",
        name="Internet Total Mbps",
        icon="mdi:speedometer",
        native_unit_of_measurement="Mbit/s",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: _traffic_value(data, "totals", "internet_total_mbps", 0),
        attrs_fn=_traffic_attrs,
    ),
    UniFiSensorDescription(
        key="wan1_total_gb",
        name="WAN1 Total GB",
        icon="mdi:database",
        native_unit_of_measurement="GB",
        state_class=SensorStateClass.TOTAL_INCREASING,
        value_fn=lambda data: _traffic_value(data, "totals", "wan1_total_gb"),
        attrs_fn=_traffic_attrs,
    ),
    UniFiSensorDescription(
        key="wan2_total_gb",
        name="WAN2 Total GB",
        icon="mdi:database",
        native_unit_of_measurement="GB",
        state_class=SensorStateClass.TOTAL_INCREASING,
        value_fn=lambda data: _traffic_value(data, "totals", "wan2_total_gb"),
        attrs_fn=_traffic_attrs,
    ),
    UniFiSensorDescription(
        key="wan1_rx_total",
        name="WAN1 RX Total",
        icon="mdi:download-network",
        device_class=SensorDeviceClass.DATA_SIZE,
        native_unit_of_measurement="GB",
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=3,
        value_fn=lambda data: _traffic_value(data, "totals", "wan1_rx_gb"),
        attrs_fn=lambda data: _traffic_total_attrs(data, "wan1_rx_gb"),
    ),
    UniFiSensorDescription(
        key="wan1_tx_total",
        name="WAN1 TX Total",
        icon="mdi:upload-network",
        device_class=SensorDeviceClass.DATA_SIZE,
        native_unit_of_measurement="GB",
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=3,
        value_fn=lambda data: _traffic_value(data, "totals", "wan1_tx_gb"),
        attrs_fn=lambda data: _traffic_total_attrs(data, "wan1_tx_gb"),
    ),
    UniFiSensorDescription(
        key="wan2_rx_total",
        name="WAN2 RX Total",
        icon="mdi:download-network",
        device_class=SensorDeviceClass.DATA_SIZE,
        native_unit_of_measurement="GB",
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=3,
        value_fn=lambda data: _traffic_value(data, "totals", "wan2_rx_gb"),
        attrs_fn=lambda data: _traffic_total_attrs(data, "wan2_rx_gb"),
    ),
    UniFiSensorDescription(
        key="wan2_tx_total",
        name="WAN2 TX Total",
        icon="mdi:upload-network",
        device_class=SensorDeviceClass.DATA_SIZE,
        native_unit_of_measurement="GB",
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=3,
        value_fn=lambda data: _traffic_value(data, "totals", "wan2_tx_gb"),
        attrs_fn=lambda data: _traffic_total_attrs(data, "wan2_tx_gb"),
    ),
    UniFiSensorDescription(
        key="internet_rx_total",
        name="Internet RX Total",
        icon="mdi:download-network",
        device_class=SensorDeviceClass.DATA_SIZE,
        native_unit_of_measurement="GB",
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=3,
        value_fn=lambda data: _traffic_value(data, "totals", "internet_rx_gb"),
        attrs_fn=lambda data: _traffic_total_attrs(data, "wan1_rx_gb", "wan2_rx_gb", "internet_rx_gb"),
    ),
    UniFiSensorDescription(
        key="internet_tx_total",
        name="Internet TX Total",
        icon="mdi:upload-network",
        device_class=SensorDeviceClass.DATA_SIZE,
        native_unit_of_measurement="GB",
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=3,
        value_fn=lambda data: _traffic_value(data, "totals", "internet_tx_gb"),
        attrs_fn=lambda data: _traffic_total_attrs(data, "wan1_tx_gb", "wan2_tx_gb", "internet_tx_gb"),
    ),
    UniFiSensorDescription(
        key="internet_total",
        name="Internet Total",
        icon="mdi:database-sync",
        device_class=SensorDeviceClass.DATA_SIZE,
        native_unit_of_measurement="GB",
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=3,
        value_fn=lambda data: _traffic_value(data, "totals", "internet_total_gb"),
        attrs_fn=lambda data: _traffic_total_attrs(data, "wan1_total_gb", "wan2_total_gb", "internet_total_gb"),
    ),

    UniFiSensorDescription(
        key="wan_event_history",
        name="WAN Event History",
        icon="mdi:timeline-clock",
        native_unit_of_measurement="events",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: int(wan_events(data).get("count") or 0),
        attrs_fn=_wan_events_attrs,
    ),
    UniFiSensorDescription(
        key="wan_last_event",
        name="WAN Last Event",
        icon="mdi:timeline-alert",
        value_fn=lambda data: (wan_events(data).get("last_event") or {}).get("event", "none"),
        attrs_fn=_wan_last_event_attrs,
    ),
    UniFiSensorDescription(
        key="wan_webhook_status",
        name="WAN Webhook Status",
        icon="mdi:webhook",
        value_fn=lambda data: "enabled" if (wan_events(data).get("webhook") or {}).get("enabled") else "disabled",
        attrs_fn=_webhook_attrs,
    ),
    UniFiSensorDescription(
        key="wan1_outages_today",
        name="WAN1 Outages Today",
        icon="mdi:alert-circle-outline",
        native_unit_of_measurement="outages",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: _event_counter(data, "wan1_outages_today"),
        attrs_fn=_wan_events_attrs,
    ),
    UniFiSensorDescription(
        key="wan2_outages_today",
        name="WAN2 Outages Today",
        icon="mdi:alert-circle-outline",
        native_unit_of_measurement="outages",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: _event_counter(data, "wan2_outages_today"),
        attrs_fn=_wan_events_attrs,
    ),
    UniFiSensorDescription(
        key="wan_total_outages_today",
        name="WAN Total Outages Today",
        icon="mdi:alert-octagon-outline",
        native_unit_of_measurement="outages",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: _event_counter(data, "wan_total_outages_today"),
        attrs_fn=_wan_events_attrs,
    ),
    UniFiSensorDescription(
        key="wan_events_today",
        name="WAN Events Today",
        icon="mdi:calendar-alert",
        native_unit_of_measurement="events",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: _event_counter(data, "events_today"),
        attrs_fn=_wan_events_attrs,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up UniFi Network Monitor sensors."""
    coordinator: UniFiNetworkMonitorCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(UniFiNetworkMonitorSensor(coordinator, entry, description) for description in SENSORS)


class UniFiNetworkMonitorSensor(UniFiNetworkMonitorEntity, SensorEntity):
    """UniFi Network Monitor sensor."""

    entity_description: UniFiSensorDescription

    def __init__(
        self,
        coordinator: UniFiNetworkMonitorCoordinator,
        entry: ConfigEntry,
        description: UniFiSensorDescription,
    ) -> None:
        """Initialize sensor."""
        super().__init__(coordinator, entry, description.key)
        self.entity_description = description
        self._attr_name = description.name

    @property
    def native_value(self) -> Any:
        """Return sensor value."""
        return self.entity_description.value_fn(self.coordinator.data or {})

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return extra attributes."""
        if self.entity_description.attrs_fn is None:
            return None
        return self.entity_description.attrs_fn(self.coordinator.data or {})
