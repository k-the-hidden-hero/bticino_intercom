"""Tests for the BNC3 ringtone mute mode select (#71).

The Home + Security app offers "ringtone on", "temporarily muted" and "always
muted". On a Classe 300X these are ``dnd_mode`` 0, 1 and 3 in the module's
do-not-disturb config, 1 with an end time in milliseconds (verified on the
device in #71). The previous "Immediate Do Not Disturb" switch wrote mode 3 and
read only mode 3, so a temporary mute set from the app showed as off.
"""

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.components.number import DOMAIN as NUMBER_DOMAIN
from homeassistant.components.select import DOMAIN as SELECT_DOMAIN
from homeassistant.components.switch import DOMAIN as SWITCH_DOMAIN
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.bticino_intercom.const import DOMAIN

from .conftest import BRIDGE_MAC, HOME_ID, _setup_integration

SELECT_UNIQUE_ID = f"{HOME_ID}_{BRIDGE_MAC}_dnd_mode"
DURATION_UNIQUE_ID = f"{HOME_ID}_{BRIDGE_MAC}_dnd_duration"
OLD_SWITCH_UNIQUE_ID = f"{HOME_ID}_{BRIDGE_MAC}_dnd_immediate"


@pytest.fixture
def dnd_config() -> dict[str, Any]:
    """The do-not-disturb config the device reports."""
    return {"dnd_enabled": True, "dnd_mode": 0}


@pytest.fixture
def mock_modules_data(mock_modules_data: dict[str, Any]) -> dict[str, Any]:
    """Make the bridge a BNC3, the Classe 300X indoor unit."""
    bridge = {**mock_modules_data[BRIDGE_MAC], "type": "BNC3", "variant": "BNC3:bnc3_bridge"}
    return {**mock_modules_data, BRIDGE_MAC: bridge}


@pytest.fixture
def mock_account(mock_account: AsyncMock, dnd_config: dict[str, Any]) -> AsyncMock:
    """Give the topology's module objects their type and name, and the config API."""
    for module in mock_account.homes[HOME_ID].modules:
        module.type = module.raw_data["type"]
        module.name = module.raw_data["name"]
    mock_account.async_get_do_not_disturb_config = AsyncMock(return_value=dnd_config)
    mock_account.async_set_do_not_disturb_config = AsyncMock(return_value={"status": "ok"})
    mock_account.async_get_professional_studio_config = AsyncMock(return_value={"ps_enabled": False})
    return mock_account


def _entity_id(hass: HomeAssistant, domain: str, unique_id: str) -> str:
    entity_id = er.async_get(hass).async_get_entity_id(domain, DOMAIN, unique_id)
    assert entity_id is not None, f"no {domain} with unique_id {unique_id}"
    return entity_id


async def _select(hass: HomeAssistant, option: str) -> None:
    await hass.services.async_call(
        SELECT_DOMAIN,
        "select_option",
        {"entity_id": _entity_id(hass, SELECT_DOMAIN, SELECT_UNIQUE_ID), "option": option},
        blocking=True,
    )


async def test_bnc3_gets_the_mode_select_and_not_the_old_switch(
    hass: HomeAssistant, mock_setup_entry: MockConfigEntry
) -> None:
    """The select offers the app's three modes; the mode-3-only switch is gone."""
    state = hass.states.get(_entity_id(hass, SELECT_DOMAIN, SELECT_UNIQUE_ID))
    assert state.attributes["options"] == ["ringtone_on", "temporarily_muted", "always_muted"]
    assert er.async_get(hass).async_get_entity_id(SWITCH_DOMAIN, DOMAIN, OLD_SWITCH_UNIQUE_ID) is None


@pytest.mark.parametrize(
    ("dnd_config", "expected"),
    [
        ({"dnd_enabled": True, "dnd_mode": 0}, "ringtone_on"),
        ({"dnd_enabled": True, "dnd_mode": 1, "dnd_end_time_duration": 1793731696000}, "temporarily_muted"),
        ({"dnd_enabled": True, "dnd_mode": 3}, "always_muted"),
        ({"dnd_enabled": False, "dnd_mode": 3}, "ringtone_on"),
    ],
)
async def test_current_option_follows_the_device(
    hass: HomeAssistant, mock_setup_entry: MockConfigEntry, expected: str
) -> None:
    """A mode set from the app or the device shows up in the select."""
    assert hass.states.get(_entity_id(hass, SELECT_DOMAIN, SELECT_UNIQUE_ID)).state == expected


@pytest.mark.parametrize("dnd_config", [{"dnd_enabled": True, "dnd_mode": 2}])
async def test_a_mode_the_select_does_not_offer_is_shown_as_unknown(
    hass: HomeAssistant, mock_setup_entry: MockConfigEntry
) -> None:
    """A schedule set from the app is not misreported as one of the three modes."""
    state = hass.states.get(_entity_id(hass, SELECT_DOMAIN, SELECT_UNIQUE_ID))
    assert state.state == "unknown"
    assert state.attributes["dnd_mode"] == 2


@pytest.mark.parametrize(
    ("option", "written"),
    [
        ("ringtone_on", {"dnd_mode": 0, "dnd_enabled": True}),
        ("always_muted", {"dnd_mode": 3, "dnd_enabled": True}),
    ],
)
async def test_selecting_a_mode_writes_it(
    hass: HomeAssistant,
    mock_setup_entry: MockConfigEntry,
    mock_account: AsyncMock,
    option: str,
    written: dict[str, Any],
) -> None:
    """Each option writes the dnd_mode the app writes."""
    await _select(hass, option)

    kwargs = mock_account.async_set_do_not_disturb_config.call_args.kwargs
    assert kwargs["dnd_config"] == written
    assert kwargs["module_id"] == BRIDGE_MAC
    assert kwargs["module_type"] == "BNC3"


async def test_temporary_mute_uses_the_duration_entity(
    hass: HomeAssistant,
    mock_setup_entry: MockConfigEntry,
    mock_account: AsyncMock,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Temporary mute ends after the duration set on the number entity."""
    now = datetime(2026, 11, 5, 12, 0, tzinfo=UTC)
    freezer.move_to(now)
    await hass.services.async_call(
        NUMBER_DOMAIN,
        "set_value",
        {"entity_id": _entity_id(hass, NUMBER_DOMAIN, DURATION_UNIQUE_ID), "value": 15},
        blocking=True,
    )

    await _select(hass, "temporarily_muted")

    assert mock_account.async_set_do_not_disturb_config.call_args.kwargs["dnd_config"] == {
        "dnd_mode": 1,
        "dnd_enabled": True,
        "dnd_duration": 15,
        "dnd_end_time_duration": int(now.timestamp() * 1000) + 15 * 60_000,
    }


async def test_a_rejected_write_is_reported(
    hass: HomeAssistant, mock_setup_entry: MockConfigEntry, mock_account: AsyncMock
) -> None:
    """A write the cloud does not accept fails the service call."""
    mock_account.async_set_do_not_disturb_config.return_value = {"status": "error"}

    with pytest.raises(HomeAssistantError):
        await _select(hass, "always_muted")


async def test_the_old_switch_is_removed_from_the_registry(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_auth_handler: AsyncMock,
    mock_account: AsyncMock,
    mock_websocket_client: AsyncMock,
    mock_signaling_client: AsyncMock,
    enable_custom_integrations: None,
) -> None:
    """2.0.4-rc1 created the switch; upgrading must not leave it behind."""
    mock_config_entry.add_to_hass(hass)
    er.async_get(hass).async_get_or_create(SWITCH_DOMAIN, DOMAIN, OLD_SWITCH_UNIQUE_ID, config_entry=mock_config_entry)

    await _setup_integration(
        hass, mock_config_entry, mock_auth_handler, mock_account, mock_websocket_client, mock_signaling_client
    )

    assert er.async_get(hass).async_get_entity_id(SWITCH_DOMAIN, DOMAIN, OLD_SWITCH_UNIQUE_ID) is None
