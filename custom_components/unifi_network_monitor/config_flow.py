"""Config flow for UniFi Network Monitor."""
from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from aiohttp import ClientSession, CookieJar

from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntry, OptionsFlowWithReload
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME, CONF_VERIFY_SSL
from homeassistant.core import HomeAssistant, callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers.aiohttp_client import async_create_clientsession

from .api import UniFiAuthError, UniFiNetworkMonitorClient, UniFiNetworkMonitorError
from .const import (
    CONF_API_KEY,
    CONF_SCAN_INTERVAL,
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
    CONF_WEBHOOK_ENABLED,
    CONF_WEBHOOK_ID,
    CONF_WEBHOOK_LOCAL_ONLY,
    DEFAULT_HOST,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_SITE,
    DEFAULT_SITE_NAME,
    DEFAULT_SNMP_ENABLED,
    DEFAULT_SNMP_PORT,
    DEFAULT_SNMP_TIMEOUT,
    DEFAULT_TELEPORT_PREFIX,
    DEFAULT_TELEPORT_CLIENT_MATCHERS,
    DEFAULT_VERIFY_SSL,
    DEFAULT_WAN1_RX_OID,
    DEFAULT_WAN1_TX_OID,
    DEFAULT_WAN2_RX_OID,
    DEFAULT_WAN2_TX_OID,
    DEFAULT_WEBHOOK_ENABLED,
    DEFAULT_WEBHOOK_LOCAL_ONLY,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)


def _schema(user_input: dict[str, Any] | None = None) -> vol.Schema:
    """Return config flow schema."""
    values = user_input or {}
    host = values.get(CONF_HOST, DEFAULT_HOST)
    return vol.Schema(
        {
            vol.Required(CONF_HOST, default=host): str,
            vol.Required(CONF_USERNAME, default=values.get(CONF_USERNAME, "")): str,
            vol.Required(CONF_PASSWORD, default=values.get(CONF_PASSWORD, "")): str,
            vol.Required(CONF_SITE, default=values.get(CONF_SITE, DEFAULT_SITE)): str,
            vol.Required(CONF_SITE_NAME, default=values.get(CONF_SITE_NAME, DEFAULT_SITE_NAME)): str,
            vol.Optional(CONF_TELEPORT_PREFIX, default=values.get(CONF_TELEPORT_PREFIX, DEFAULT_TELEPORT_PREFIX)): str,
            vol.Optional(CONF_TELEPORT_CLIENT_MATCHERS, default=values.get(CONF_TELEPORT_CLIENT_MATCHERS, DEFAULT_TELEPORT_CLIENT_MATCHERS)): str,
            vol.Optional(CONF_API_KEY, default=values.get(CONF_API_KEY, "")): str,
            vol.Required(CONF_VERIFY_SSL, default=values.get(CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL)): bool,
            vol.Required(CONF_SCAN_INTERVAL, default=values.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)): vol.All(
                vol.Coerce(int), vol.Range(min=30, max=3600)
            ),
            vol.Optional(CONF_SNMP_ENABLED, default=values.get(CONF_SNMP_ENABLED, DEFAULT_SNMP_ENABLED)): bool,
            vol.Optional(CONF_SNMP_HOST, default=values.get(CONF_SNMP_HOST, host)): str,
            vol.Optional(CONF_SNMP_COMMUNITY, default=values.get(CONF_SNMP_COMMUNITY, "")): str,
            vol.Optional(CONF_SNMP_PORT, default=values.get(CONF_SNMP_PORT, DEFAULT_SNMP_PORT)): vol.All(
                vol.Coerce(int), vol.Range(min=1, max=65535)
            ),
            vol.Optional(CONF_SNMP_TIMEOUT, default=values.get(CONF_SNMP_TIMEOUT, DEFAULT_SNMP_TIMEOUT)): vol.All(
                vol.Coerce(int), vol.Range(min=1, max=30)
            ),
            vol.Optional(CONF_WAN1_RX_OID, default=values.get(CONF_WAN1_RX_OID, DEFAULT_WAN1_RX_OID)): str,
            vol.Optional(CONF_WAN1_TX_OID, default=values.get(CONF_WAN1_TX_OID, DEFAULT_WAN1_TX_OID)): str,
            vol.Optional(CONF_WAN2_RX_OID, default=values.get(CONF_WAN2_RX_OID, DEFAULT_WAN2_RX_OID)): str,
            vol.Optional(CONF_WAN2_TX_OID, default=values.get(CONF_WAN2_TX_OID, DEFAULT_WAN2_TX_OID)): str,
            vol.Optional(CONF_WEBHOOK_ENABLED, default=values.get(CONF_WEBHOOK_ENABLED, DEFAULT_WEBHOOK_ENABLED)): bool,
            vol.Optional(CONF_WEBHOOK_LOCAL_ONLY, default=values.get(CONF_WEBHOOK_LOCAL_ONLY, DEFAULT_WEBHOOK_LOCAL_ONLY)): bool,
        }
    )


def _options_schema(config_entry: ConfigEntry) -> vol.Schema:
    """Return options flow schema."""
    data = config_entry.data
    options = config_entry.options

    def current(key: str, default: Any = "") -> Any:
        return options.get(key, data.get(key, default))

    snmp_host_default = current(CONF_SNMP_HOST, data.get(CONF_HOST, DEFAULT_HOST))
    return vol.Schema(
        {
            vol.Optional(
                CONF_TELEPORT_PREFIX,
                default=current(CONF_TELEPORT_PREFIX, DEFAULT_TELEPORT_PREFIX),
            ): str,
            vol.Optional(
                CONF_TELEPORT_CLIENT_MATCHERS,
                default=current(CONF_TELEPORT_CLIENT_MATCHERS, DEFAULT_TELEPORT_CLIENT_MATCHERS),
            ): str,
            vol.Optional(
                CONF_API_KEY,
                default=current(CONF_API_KEY, ""),
            ): str,
            vol.Required(
                CONF_SCAN_INTERVAL,
                default=current(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
            ): vol.All(vol.Coerce(int), vol.Range(min=30, max=3600)),
            vol.Optional(CONF_SNMP_ENABLED, default=current(CONF_SNMP_ENABLED, DEFAULT_SNMP_ENABLED)): bool,
            vol.Optional(CONF_SNMP_HOST, default=snmp_host_default): str,
            vol.Optional(CONF_SNMP_COMMUNITY, default=current(CONF_SNMP_COMMUNITY, "")): str,
            vol.Optional(CONF_SNMP_PORT, default=current(CONF_SNMP_PORT, DEFAULT_SNMP_PORT)): vol.All(
                vol.Coerce(int), vol.Range(min=1, max=65535)
            ),
            vol.Optional(CONF_SNMP_TIMEOUT, default=current(CONF_SNMP_TIMEOUT, DEFAULT_SNMP_TIMEOUT)): vol.All(
                vol.Coerce(int), vol.Range(min=1, max=30)
            ),
            vol.Optional(CONF_WAN1_RX_OID, default=current(CONF_WAN1_RX_OID, DEFAULT_WAN1_RX_OID)): str,
            vol.Optional(CONF_WAN1_TX_OID, default=current(CONF_WAN1_TX_OID, DEFAULT_WAN1_TX_OID)): str,
            vol.Optional(CONF_WAN2_RX_OID, default=current(CONF_WAN2_RX_OID, DEFAULT_WAN2_RX_OID)): str,
            vol.Optional(CONF_WAN2_TX_OID, default=current(CONF_WAN2_TX_OID, DEFAULT_WAN2_TX_OID)): str,
            vol.Optional(CONF_WEBHOOK_ENABLED, default=current(CONF_WEBHOOK_ENABLED, DEFAULT_WEBHOOK_ENABLED)): bool,
            vol.Optional(CONF_WEBHOOK_LOCAL_ONLY, default=current(CONF_WEBHOOK_LOCAL_ONLY, DEFAULT_WEBHOOK_LOCAL_ONLY)): bool,
            vol.Optional(CONF_WEBHOOK_ID, default=current(CONF_WEBHOOK_ID, "")): str,
        }
    )


def _clean_optional_settings(user_input: dict[str, Any]) -> dict[str, Any]:
    """Normalize optional settings."""
    cleaned = dict(user_input)
    cleaned[CONF_API_KEY] = cleaned.get(CONF_API_KEY) or ""
    cleaned[CONF_TELEPORT_PREFIX] = (
        cleaned.get(CONF_TELEPORT_PREFIX) or DEFAULT_TELEPORT_PREFIX
    ).strip()
    cleaned[CONF_TELEPORT_CLIENT_MATCHERS] = (
        cleaned.get(CONF_TELEPORT_CLIENT_MATCHERS) or DEFAULT_TELEPORT_CLIENT_MATCHERS
    ).strip()
    cleaned[CONF_SNMP_HOST] = UniFiNetworkMonitorClient._clean_host(
        cleaned.get(CONF_SNMP_HOST) or cleaned.get(CONF_HOST) or DEFAULT_HOST
    )
    cleaned[CONF_SNMP_COMMUNITY] = cleaned.get(CONF_SNMP_COMMUNITY) or ""
    cleaned[CONF_SNMP_ENABLED] = bool(cleaned.get(CONF_SNMP_ENABLED) and cleaned[CONF_SNMP_COMMUNITY])
    for key in (CONF_WAN1_RX_OID, CONF_WAN1_TX_OID, CONF_WAN2_RX_OID, CONF_WAN2_TX_OID):
        cleaned[key] = (cleaned.get(key) or "").strip()
    cleaned[CONF_WEBHOOK_ENABLED] = bool(cleaned.get(CONF_WEBHOOK_ENABLED, DEFAULT_WEBHOOK_ENABLED))
    cleaned[CONF_WEBHOOK_LOCAL_ONLY] = bool(cleaned.get(CONF_WEBHOOK_LOCAL_ONLY, DEFAULT_WEBHOOK_LOCAL_ONLY))
    if CONF_WEBHOOK_ID in cleaned:
        cleaned[CONF_WEBHOOK_ID] = (cleaned.get(CONF_WEBHOOK_ID) or "").strip()
    return cleaned


async def _validate(hass: HomeAssistant, user_input: dict[str, Any]) -> dict[str, str]:
    """Validate the user input allows us to connect."""
    verify_ssl = bool(user_input.get(CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL))
    session: ClientSession = async_create_clientsession(
        hass,
        verify_ssl=verify_ssl,
        auto_cleanup=False,
        cookie_jar=CookieJar(unsafe=True),
    )
    client = UniFiNetworkMonitorClient(
        session=session,
        host=user_input[CONF_HOST],
        username=user_input[CONF_USERNAME],
        password=user_input[CONF_PASSWORD],
        site=user_input[CONF_SITE],
        site_name=user_input[CONF_SITE_NAME],
        teleport_prefix=user_input.get(CONF_TELEPORT_PREFIX) or DEFAULT_TELEPORT_PREFIX,
        teleport_client_matchers=user_input.get(CONF_TELEPORT_CLIENT_MATCHERS) or DEFAULT_TELEPORT_CLIENT_MATCHERS,
        api_key=user_input.get(CONF_API_KEY) or None,
    )
    try:
        await client.async_login(force=True)
        status, _body = await client.async_get_json(
            f"/proxy/network/api/s/{user_input[CONF_SITE]}/stat/health",
            raise_for_status=False,
        )
        if status != 200:
            raise UniFiNetworkMonitorError(f"Health endpoint returned HTTP {status}")
    finally:
        await session.close()

    return {"title": f"{client.host} ({user_input[CONF_SITE]})"}


class UniFiNetworkMonitorConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for UniFi Network Monitor."""

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> config_entries.OptionsFlow:
        """Create the options flow."""
        return UniFiNetworkMonitorOptionsFlowHandler()

    async def async_step_reconfigure(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Handle reconfiguration of an existing config entry."""
        config_entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}

        if user_input is not None:
            cleaned = _clean_optional_settings(user_input)
            cleaned[CONF_HOST] = UniFiNetworkMonitorClient._clean_host(cleaned[CONF_HOST])
            await self.async_set_unique_id(f"{cleaned[CONF_HOST]}:{cleaned[CONF_SITE]}")
            self._abort_if_unique_id_mismatch(reason="wrong_site")

            try:
                await _validate(self.hass, cleaned)
            except UniFiAuthError:
                errors["base"] = "invalid_auth"
            except UniFiNetworkMonitorError:
                errors["base"] = "cannot_connect"
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Unexpected error while reconfiguring UniFi Network Monitor")
                errors["base"] = "unknown"
            else:
                scan_interval = cleaned.pop(CONF_SCAN_INTERVAL)
                self.hass.config_entries.async_update_entry(
                    config_entry,
                    options={**config_entry.options, CONF_SCAN_INTERVAL: scan_interval},
                )
                return self.async_update_reload_and_abort(
                    config_entry,
                    data_updates=cleaned,
                )

        values = {
            **config_entry.data,
            CONF_SCAN_INTERVAL: config_entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
        }
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=_schema(values),
            errors=errors,
        )

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            cleaned = _clean_optional_settings(user_input)
            cleaned[CONF_HOST] = UniFiNetworkMonitorClient._clean_host(cleaned[CONF_HOST])
            await self.async_set_unique_id(f"{cleaned[CONF_HOST]}:{cleaned[CONF_SITE]}")
            self._abort_if_unique_id_configured()

            try:
                info = await _validate(self.hass, cleaned)
            except UniFiAuthError:
                errors["base"] = "invalid_auth"
            except UniFiNetworkMonitorError:
                errors["base"] = "cannot_connect"
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Unexpected error while setting up UniFi Network Monitor")
                errors["base"] = "unknown"
            else:
                scan_interval = cleaned.pop(CONF_SCAN_INTERVAL)
                return self.async_create_entry(
                    title=info["title"],
                    data=cleaned,
                    options={CONF_SCAN_INTERVAL: scan_interval},
                )

        return self.async_show_form(
            step_id="user",
            data_schema=_schema(user_input),
            errors=errors,
        )


class UniFiNetworkMonitorOptionsFlowHandler(OptionsFlowWithReload):
    """Handle options for UniFi Network Monitor."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Manage the options."""
        if user_input is not None:
            return self.async_create_entry(data=_clean_optional_settings(user_input))

        return self.async_show_form(
            step_id="init",
            data_schema=_options_schema(self.config_entry),
        )
