"""Tests for utility functions (cleanup_orphaned_entities)."""

from unittest.mock import MagicMock

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.bticino_intercom.const import DOMAIN
from custom_components.bticino_intercom.utils import cleanup_orphaned_entities

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")


@pytest.fixture
def registered_lock(hass: HomeAssistant, mock_config_entry: MockConfigEntry) -> er.RegistryEntry:
    """Register a lock entity as if created by a previous healthy setup."""
    mock_config_entry.add_to_hass(hass)
    ent_reg = er.async_get(hass)
    return ent_reg.async_get_or_create(
        "lock",
        DOMAIN,
        f"{mock_config_entry.entry_id}_lock_module1",
        config_entry=mock_config_entry,
    )


async def test_cleanup_removes_stale_entities(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    registered_lock: er.RegistryEntry,
) -> None:
    """An entity whose unique_id is no longer built must be removed."""
    current = [MagicMock(unique_id=f"{mock_config_entry.entry_id}_lock_module2")]

    cleanup_orphaned_entities(hass, mock_config_entry.entry_id, "lock", current)

    assert er.async_get(hass).async_get(registered_lock.entity_id) is None


async def test_cleanup_keeps_current_entities(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    registered_lock: er.RegistryEntry,
) -> None:
    """An entity rebuilt in the current setup pass must survive cleanup."""
    current = [MagicMock(unique_id=registered_lock.unique_id)]

    cleanup_orphaned_entities(hass, mock_config_entry.entry_id, "lock", current)

    assert er.async_get(hass).async_get(registered_lock.entity_id) is not None


async def test_cleanup_skipped_when_no_entities_created(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    registered_lock: er.RegistryEntry,
) -> None:
    """Zero entities built means degraded cloud data, not module removal (issue #68).

    Cleanup must be skipped entirely so a transient empty/partial topology
    at startup cannot permanently delete the user's registered entities.
    """
    cleanup_orphaned_entities(hass, mock_config_entry.entry_id, "lock", [])

    assert er.async_get(hass).async_get(registered_lock.entity_id) is not None
