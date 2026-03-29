"""Base entity for BTicino devices."""

from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import BticinoIntercomCoordinator


class BticinoEntity(CoordinatorEntity[BticinoIntercomCoordinator]):
    """Base entity for BTicino devices."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: BticinoIntercomCoordinator, module_id: str) -> None:
        """Initialize the base entity."""
        super().__init__(coordinator)
        self._module_id = module_id
        self._module_data = coordinator.data.get("modules", {}).get(module_id, {})
        self._bridge_id = self._module_data.get("bridge")

    @property
    def device_info(self) -> DeviceInfo:
        """Return device info linking to the main bridge device."""
        device_name = (
            f"BTicino Intercom - {self.coordinator.home_name}" if self.coordinator.home_name else "BTicino Intercom"
        )
        bridge_module_data = self.coordinator.data.get("modules", {}).get(self.coordinator.main_device_id)
        model = bridge_module_data.get("type") if bridge_module_data else None

        return DeviceInfo(
            identifiers={(DOMAIN, self.coordinator.main_device_id)},
            name=device_name,
            manufacturer="BTicino",
            model=model,
        )
