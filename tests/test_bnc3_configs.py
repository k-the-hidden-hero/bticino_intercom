"""Discovery tests for the BNC3 DND / Professional Studio platforms.

These entities are built from `getconfigs`, an endpoint unrelated to the
topology every other platform uses, and they exist only for BNC3 modules. The
guard that keeps them away from every other installation is the only thing
standing between those users and 670 lines they have no use for, so it is worth
pinning.
"""

from unittest.mock import AsyncMock

from homeassistant.components.number import DOMAIN as NUMBER_DOMAIN
from homeassistant.components.switch import DOMAIN as SWITCH_DOMAIN
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry


async def test_no_switch_or_number_without_a_bnc3(
    hass: HomeAssistant,
    mock_setup_entry: MockConfigEntry,
) -> None:
    """A 100X home (BNC1 bridge) gets neither platform."""
    ent_reg = er.async_get(hass)
    entries = er.async_entries_for_config_entry(ent_reg, mock_setup_entry.entry_id)
    domains = {entry.domain for entry in entries}

    assert SWITCH_DOMAIN not in domains
    assert NUMBER_DOMAIN not in domains


async def test_getconfigs_is_not_called_without_a_bnc3(
    hass: HomeAssistant,
    mock_setup_entry: MockConfigEntry,
    mock_account: AsyncMock,
) -> None:
    """No BNC3 means no config request at all.

    The professional-studio lookup runs inside the platform's setup and is not
    wrapped in a try/except, so on a home that has no BNC3 it must never be
    reached — a failure there would take the whole platform setup down with it.
    """
    assert not mock_account.async_get_professional_studio_config.called
    assert not mock_account.async_get_do_not_disturb_config.called


async def test_eos_home_gets_neither_platform(
    hass: HomeAssistant,
    mock_setup_entry_eos: MockConfigEntry,
) -> None:
    """Same for a 300 EOS home, whose bridge is BNCX rather than BNC1."""
    ent_reg = er.async_get(hass)
    entries = er.async_entries_for_config_entry(ent_reg, mock_setup_entry_eos.entry_id)
    domains = {entry.domain for entry in entries}

    assert SWITCH_DOMAIN not in domains
    assert NUMBER_DOMAIN not in domains
