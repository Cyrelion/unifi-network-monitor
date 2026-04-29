"""Data coordinator for UniFi Network Monitor."""
from __future__ import annotations

import copy
import logging
import time
from collections.abc import Iterable
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import UniFiAuthError, UniFiNetworkMonitorClient, UniFiRequestError
from .const import DEFAULT_SCAN_INTERVAL, DEFAULT_WEBHOOK_HISTORY_LIMIT, DOMAIN, NAME

_LOGGER = logging.getLogger(__name__)


class UniFiNetworkMonitorCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Coordinate updates for UniFi Network Monitor."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        api: UniFiNetworkMonitorClient,
    ) -> None:
        """Initialize coordinator."""
        self.entry = entry
        self.api = api
        self._store: Store[dict[str, Any]] = Store(
            hass,
            1,
            f"{DOMAIN}_{entry.entry_id}_wan_event_history",
        )
        self._wan_events: list[dict[str, Any]] = []
        self._webhook_info: dict[str, Any] = {
            "enabled": False,
            "webhook_id": "",
            "local_only": False,
        }
        scan_interval = int(entry.options.get("scan_interval", DEFAULT_SCAN_INTERVAL))
        super().__init__(
            hass,
            _LOGGER,
            name=f"{NAME} {entry.title}",
            update_interval=timedelta(seconds=scan_interval),
        )

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch data from UniFi Network.

        After the first successful refresh we deliberately keep returning the
        last known data on transient errors. Otherwise Home Assistant marks all
        CoordinatorEntity based sensors as unavailable whenever one poll fails.
        The original YAML/Bash package behaved similarly because it kept using
        the last cache file until the next successful update.
        """
        try:
            data = await self.api.async_fetch_all()
            if isinstance(data, dict):
                meta = data.setdefault("meta", {})
                meta["stale"] = False
                meta["last_success_at"] = int(time.time())
                meta.pop("stale_reason", None)
                meta.pop("stale_error", None)
                meta.pop("stale_at", None)
                self._merge_local_state(data)
            return data
        except UniFiAuthError as err:
            if self.data:
                _LOGGER.warning(
                    "UniFi Network Monitor auth/update failed, keeping last known data: %s",
                    err,
                )
                return await self._stale_data("auth_error", err)
            raise ConfigEntryAuthFailed(str(err)) from err
        except UniFiRequestError as err:
            if self.data:
                _LOGGER.warning(
                    "UniFi Network Monitor update failed, keeping last known data: %s",
                    err,
                )
                return await self._stale_data("request_error", err)
            raise UpdateFailed(str(err)) from err
        except Exception as err:  # noqa: BLE001
            if self.data:
                _LOGGER.exception(
                    "Unexpected UniFi Network Monitor update error, keeping last known data"
                )
                return await self._stale_data("unexpected_error", err)
            raise UpdateFailed(str(err)) from err

    async def _stale_data(self, reason: str, err: Exception) -> dict[str, Any]:
        """Return a copy of the previous payload marked as stale.

        API/auth failures should not freeze the SNMP traffic counters. SNMP is
        independent from the UniFi Network web session, so we keep trying to
        refresh traffic data even while the API part is stale.
        """
        data = copy.deepcopy(self.data or {})
        meta = data.setdefault("meta", {})
        stale_at = int(time.time())
        meta["stale"] = True
        meta["stale_reason"] = reason
        meta["stale_error"] = str(err)
        meta["stale_at"] = stale_at

        try:
            traffic = await self.api.async_fetch_snmp_traffic()
        except Exception as snmp_err:  # noqa: BLE001
            _LOGGER.warning(
                "SNMP traffic update also failed while API data is stale: %s",
                snmp_err,
            )
            traffic = data.get("traffic")
            if isinstance(traffic, dict):
                traffic_meta = traffic.setdefault("meta", {})
                traffic_meta["stale"] = True
                traffic_meta["stale_reason"] = "snmp_error"
                traffic_meta["stale_error"] = str(snmp_err)
                traffic_meta["stale_at"] = stale_at
        else:
            # Replace the old traffic snapshot with a fresh SNMP snapshot. Do not
            # copy the API auth stale reason into traffic metadata.
            data["traffic"] = traffic

        vpn_status = data.get("vpn_status")
        if isinstance(vpn_status, dict):
            vpn_meta = vpn_status.setdefault("meta", {})
            vpn_meta["stale"] = True
            vpn_meta["stale_reason"] = reason
            vpn_meta["stale_error"] = str(err)
            vpn_meta["stale_at"] = stale_at

        self._merge_local_state(data)
        return data


    async def async_load_local_state(self) -> None:
        """Load stored local event history."""
        stored = await self._store.async_load()
        if not isinstance(stored, dict):
            return
        events = stored.get("events")
        if isinstance(events, list):
            self._wan_events = [row for row in events if isinstance(row, dict)][:DEFAULT_WEBHOOK_HISTORY_LIMIT]

    def set_webhook_info(self, *, enabled: bool, webhook_id: str, local_only: bool) -> None:
        """Set runtime webhook information exposed via sensors."""
        self._webhook_info = {
            "enabled": enabled,
            "webhook_id": webhook_id,
            "local_only": local_only,
        }
        if self.data:
            data = copy.deepcopy(self.data)
            self._merge_local_state(data)
            self.async_set_updated_data(data)

    async def async_add_wan_event(self, event: dict[str, Any]) -> None:
        """Add a WAN webhook event to local history and update entities."""
        self._wan_events = [dict(event), *self._wan_events][:DEFAULT_WEBHOOK_HISTORY_LIMIT]
        await self._store.async_save({"events": self._wan_events})
        if self.data:
            data = copy.deepcopy(self.data)
            self._merge_local_state(data)
            self.async_set_updated_data(data)

    async def async_clear_wan_event_history(self) -> None:
        """Clear locally stored WAN webhook events."""
        self._wan_events = []
        await self._store.async_save({"events": []})
        if self.data:
            data = copy.deepcopy(self.data)
            self._merge_local_state(data)
            self.async_set_updated_data(data)

    def _merge_local_state(self, data: dict[str, Any]) -> None:
        """Inject local webhook/event-history state into the coordinator payload."""
        events = copy.deepcopy(self._wan_events)
        data["wan_event_history"] = {
            "events": events,
            "count": len(events),
            "last_event": events[0] if events else {},
            "counters": self._event_counters(events),
            "webhook": dict(self._webhook_info),
            "meta": {
                "history_limit": DEFAULT_WEBHOOK_HISTORY_LIMIT,
                "updated_at": int(time.time()),
            },
        }

    @staticmethod
    def _event_counters(events: Iterable[dict[str, Any]]) -> dict[str, int]:
        """Return today's WAN event counters from stored webhook history."""
        today = dt_util.now().date().isoformat()
        counters = {
            "wan1_outages_today": 0,
            "wan2_outages_today": 0,
            "wan_total_outages_today": 0,
            "events_today": 0,
        }
        for row in events:
            if row.get("date") != today:
                continue
            counters["events_today"] += 1
            if row.get("kind") != "down":
                continue
            wan = str(row.get("wan") or "").upper()
            if wan == "WAN1":
                counters["wan1_outages_today"] += 1
            elif wan == "WAN2":
                counters["wan2_outages_today"] += 1
            counters["wan_total_outages_today"] += 1
        return counters
