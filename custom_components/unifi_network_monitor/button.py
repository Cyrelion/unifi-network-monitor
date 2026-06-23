"""Button platform for UniFi Network Monitor."""
from __future__ import annotations

from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import UniFiNetworkMonitorCoordinator
from .entity import UniFiNetworkMonitorEntity


BUTTONS: tuple[ButtonEntityDescription, ...] = (
    ButtonEntityDescription(key="refresh", name="Refresh", icon="mdi:refresh"),
    ButtonEntityDescription(key="clear_wan_event_history", name="Clear WAN Event History", icon="mdi:timeline-remove"),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up UniFi Network Monitor buttons."""
    coordinator: UniFiNetworkMonitorCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(UniFiNetworkMonitorButton(coordinator, entry, description) for description in BUTTONS)


class UniFiNetworkMonitorButton(UniFiNetworkMonitorEntity, ButtonEntity):
    """UniFi Network Monitor button."""

    entity_description: ButtonEntityDescription

    def __init__(
        self,
        coordinator: UniFiNetworkMonitorCoordinator,
        entry: ConfigEntry,
        description: ButtonEntityDescription,
    ) -> None:
        """Initialize button."""
        super().__init__(coordinator, entry, description.key)
        self.entity_description = description
        self._attr_name = description.name

    async def async_press(self) -> None:
        """Run button action."""
        if self.entity_description.key == "clear_wan_event_history":
            await self.coordinator.async_clear_wan_event_history()
            return
        await self.coordinator.async_request_refresh()
