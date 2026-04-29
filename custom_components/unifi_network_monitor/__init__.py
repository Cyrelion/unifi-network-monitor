"""The UniFi Network Monitor integration."""
from __future__ import annotations

from aiohttp import CookieJar

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME, CONF_VERIFY_SSL, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_create_clientsession

from .api import UniFiNetworkMonitorClient
from .const import (
    CONF_API_KEY,
    CONF_SITE,
    CONF_SITE_NAME,
    CONF_SNMP_COMMUNITY,
    CONF_SNMP_ENABLED,
    CONF_SNMP_HOST,
    CONF_SNMP_PORT,
    CONF_SNMP_TIMEOUT,
    CONF_TELEPORT_PREFIX,
    CONF_TELEPORT_CLIENT_MATCHERS,
    CONF_WAN1_RX_OID,
    CONF_WAN1_TX_OID,
    CONF_WAN2_RX_OID,
    CONF_WAN2_TX_OID,
    DEFAULT_SITE,
    DEFAULT_SITE_NAME,
    DEFAULT_SNMP_ENABLED,
    DEFAULT_SNMP_PORT,
    DEFAULT_SNMP_TIMEOUT,
    DEFAULT_TELEPORT_PREFIX,
    DEFAULT_TELEPORT_CLIENT_MATCHERS,
    DEFAULT_WAN1_RX_OID,
    DEFAULT_WAN1_TX_OID,
    DEFAULT_WAN2_RX_OID,
    DEFAULT_WAN2_TX_OID,
    DOMAIN,
)
from .coordinator import UniFiNetworkMonitorCoordinator
from .webhook import async_register_webhook, async_unregister_webhook

PLATFORMS: list[Platform] = [Platform.SENSOR, Platform.BINARY_SENSOR, Platform.BUTTON]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up UniFi Network Monitor from a config entry."""
    verify_ssl = entry.data.get(CONF_VERIFY_SSL, False)
    session = async_create_clientsession(
        hass,
        verify_ssl=verify_ssl,
        cookie_jar=CookieJar(unsafe=True),
    )

    api = UniFiNetworkMonitorClient(
        session=session,
        host=entry.data[CONF_HOST],
        username=entry.data[CONF_USERNAME],
        password=entry.data[CONF_PASSWORD],
        site=entry.data.get(CONF_SITE, DEFAULT_SITE),
        site_name=entry.data.get(CONF_SITE_NAME, DEFAULT_SITE_NAME),
        teleport_prefix=entry.options.get(
            CONF_TELEPORT_PREFIX,
            entry.data.get(CONF_TELEPORT_PREFIX, DEFAULT_TELEPORT_PREFIX),
        ),
        teleport_client_matchers=entry.options.get(
            CONF_TELEPORT_CLIENT_MATCHERS,
            entry.data.get(CONF_TELEPORT_CLIENT_MATCHERS, DEFAULT_TELEPORT_CLIENT_MATCHERS),
        ),
        api_key=entry.options.get(CONF_API_KEY, entry.data.get(CONF_API_KEY)),
        snmp_enabled=entry.options.get(
            CONF_SNMP_ENABLED,
            entry.data.get(CONF_SNMP_ENABLED, DEFAULT_SNMP_ENABLED),
        ),
        snmp_host=entry.options.get(CONF_SNMP_HOST, entry.data.get(CONF_SNMP_HOST, entry.data[CONF_HOST])),
        snmp_community=entry.options.get(CONF_SNMP_COMMUNITY, entry.data.get(CONF_SNMP_COMMUNITY, "")),
        snmp_port=entry.options.get(CONF_SNMP_PORT, entry.data.get(CONF_SNMP_PORT, DEFAULT_SNMP_PORT)),
        snmp_timeout=entry.options.get(CONF_SNMP_TIMEOUT, entry.data.get(CONF_SNMP_TIMEOUT, DEFAULT_SNMP_TIMEOUT)),
        snmp_oids={
            "wan1_rx": entry.options.get(CONF_WAN1_RX_OID, entry.data.get(CONF_WAN1_RX_OID, DEFAULT_WAN1_RX_OID)),
            "wan1_tx": entry.options.get(CONF_WAN1_TX_OID, entry.data.get(CONF_WAN1_TX_OID, DEFAULT_WAN1_TX_OID)),
            "wan2_rx": entry.options.get(CONF_WAN2_RX_OID, entry.data.get(CONF_WAN2_RX_OID, DEFAULT_WAN2_RX_OID)),
            "wan2_tx": entry.options.get(CONF_WAN2_TX_OID, entry.data.get(CONF_WAN2_TX_OID, DEFAULT_WAN2_TX_OID)),
        },
    )

    coordinator = UniFiNetworkMonitorCoordinator(hass, entry, api)
    await coordinator.async_load_local_state()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    await async_register_webhook(hass, entry, coordinator)
    await coordinator.async_config_entry_first_refresh()

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        await async_unregister_webhook(hass, entry)
        hass.data[DOMAIN].pop(entry.entry_id)
    return unload_ok
