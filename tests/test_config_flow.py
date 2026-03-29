"""Tests for the BTicino config flow."""

from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pybticino.exceptions import ApiError, AuthError

from custom_components.bticino_intercom.const import DOMAIN


async def test_user_step_shows_form(hass: HomeAssistant) -> None:
    """Test the initial user step shows the credentials form."""
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    assert result["errors"] == {}


async def test_user_step_invalid_auth(hass: HomeAssistant) -> None:
    """Test we handle invalid credentials."""
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})

    with patch(
        "custom_components.bticino_intercom.config_flow.validate_input",
        side_effect=AuthError("Invalid credentials"),
    ):
        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {"username": "user@example.com", "password": "wrong"},
        )

    assert result2["type"] is FlowResultType.FORM
    assert result2["errors"] == {"base": "invalid_auth"}


async def test_user_step_cannot_connect(hass: HomeAssistant) -> None:
    """Test we handle API connection errors."""
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})

    with patch(
        "custom_components.bticino_intercom.config_flow.validate_input",
        side_effect=ApiError(503, "Connection timeout"),
    ):
        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {"username": "user@example.com", "password": "test"},
        )

    assert result2["type"] is FlowResultType.FORM
    assert result2["errors"] == {"base": "cannot_connect"}


async def test_user_step_unknown_error(hass: HomeAssistant) -> None:
    """Test we handle unexpected exceptions."""
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})

    with patch(
        "custom_components.bticino_intercom.config_flow.validate_input",
        side_effect=RuntimeError("Something unexpected"),
    ):
        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {"username": "user@example.com", "password": "test"},
        )

    assert result2["type"] is FlowResultType.FORM
    assert result2["errors"] == {"base": "unknown"}


async def test_user_step_no_homes(hass: HomeAssistant) -> None:
    """Test we abort when no homes are found."""
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})

    mock_auth = AsyncMock()
    mock_account = AsyncMock()
    mock_account.homes = {}

    with (
        patch(
            "custom_components.bticino_intercom.config_flow.validate_input",
            return_value=mock_auth,
        ),
        patch(
            "custom_components.bticino_intercom.config_flow.AsyncAccount",
            return_value=mock_account,
        ),
    ):
        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {"username": "user@example.com", "password": "test"},
        )

    assert result2["type"] is FlowResultType.ABORT
    assert result2["reason"] == "no_homes_found"


async def test_single_home_goes_to_options(hass: HomeAssistant) -> None:
    """Test that a single home skips home selection and goes to options."""
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})

    mock_auth = AsyncMock()
    mock_home = MagicMock()
    mock_home.id = "home_123"
    mock_home.name = "My Home"
    mock_home.modules = []

    mock_account = AsyncMock()
    mock_account.homes = {"home_123": mock_home}

    with (
        patch(
            "custom_components.bticino_intercom.config_flow.validate_input",
            return_value=mock_auth,
        ),
        patch(
            "custom_components.bticino_intercom.config_flow.AsyncAccount",
            return_value=mock_account,
        ),
    ):
        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {"username": "user@example.com", "password": "test"},
        )

    # Should go directly to init_options
    assert result2["type"] is FlowResultType.FORM
    assert result2["step_id"] == "init_options"


async def test_single_home_creates_entry(hass: HomeAssistant) -> None:
    """Test successful flow with single home creates an entry."""
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})

    mock_auth = AsyncMock()
    mock_home = MagicMock()
    mock_home.id = "home_123"
    mock_home.name = "My Home"
    mock_home.modules = []

    mock_account = AsyncMock()
    mock_account.homes = {"home_123": mock_home}

    with (
        patch(
            "custom_components.bticino_intercom.config_flow.validate_input",
            return_value=mock_auth,
        ),
        patch(
            "custom_components.bticino_intercom.config_flow.AsyncAccount",
            return_value=mock_account,
        ),
    ):
        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {"username": "user@example.com", "password": "test"},
        )

    # Complete options step - mock setup to prevent real setup during teardown
    with patch(
        "custom_components.bticino_intercom.async_setup_entry",
        return_value=True,
    ):
        result3 = await hass.config_entries.flow.async_configure(
            result2["flow_id"],
            {"light_as_lock": False},
        )
    assert result3["type"] is FlowResultType.CREATE_ENTRY
    assert result3["title"] == "My Home"
    assert result3["data"]["home_id"] == "home_123"
    assert result3["data"]["username"] == "user@example.com"
    assert result3["options"]["light_as_lock"] is False


async def test_multiple_homes_shows_selection(hass: HomeAssistant) -> None:
    """Test that multiple homes shows the home selection step."""
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})

    mock_auth = AsyncMock()

    mock_home1 = MagicMock()
    mock_home1.id = "home_1"
    mock_home1.name = "Home One"
    mock_home1.modules = [MagicMock(), MagicMock()]

    mock_home2 = MagicMock()
    mock_home2.id = "home_2"
    mock_home2.name = "Home Two"
    mock_home2.modules = [MagicMock()]

    mock_account = AsyncMock()
    mock_account.homes = {"home_1": mock_home1, "home_2": mock_home2}

    with (
        patch(
            "custom_components.bticino_intercom.config_flow.validate_input",
            return_value=mock_auth,
        ),
        patch(
            "custom_components.bticino_intercom.config_flow.AsyncAccount",
            return_value=mock_account,
        ),
    ):
        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {"username": "user@example.com", "password": "test"},
        )

    assert result2["type"] is FlowResultType.FORM
    assert result2["step_id"] == "select_home"


async def test_multiple_homes_select_and_create(hass: HomeAssistant) -> None:
    """Test selecting a home from multiple homes creates an entry."""
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})

    mock_auth = AsyncMock()

    mock_home1 = MagicMock()
    mock_home1.id = "home_1"
    mock_home1.name = "Home One"
    mock_home1.modules = [MagicMock()]

    mock_home2 = MagicMock()
    mock_home2.id = "home_2"
    mock_home2.name = "Home Two"
    mock_home2.modules = []

    mock_account = AsyncMock()
    mock_account.homes = {"home_1": mock_home1, "home_2": mock_home2}

    with (
        patch(
            "custom_components.bticino_intercom.config_flow.validate_input",
            return_value=mock_auth,
        ),
        patch(
            "custom_components.bticino_intercom.config_flow.AsyncAccount",
            return_value=mock_account,
        ),
    ):
        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {"username": "user@example.com", "password": "test"},
        )

    # Select home_2
    result3 = await hass.config_entries.flow.async_configure(
        result2["flow_id"],
        {"home_id": "home_2"},
    )

    assert result3["type"] is FlowResultType.FORM
    assert result3["step_id"] == "init_options"

    # Complete options - mock setup to prevent real setup during teardown
    with patch(
        "custom_components.bticino_intercom.async_setup_entry",
        return_value=True,
    ):
        result4 = await hass.config_entries.flow.async_configure(
            result3["flow_id"],
            {"light_as_lock": True},
        )
    assert result4["type"] is FlowResultType.CREATE_ENTRY
    assert result4["title"] == "Home Two"
    assert result4["data"]["home_id"] == "home_2"
    assert result4["options"]["light_as_lock"] is True


async def test_duplicate_entry_aborts(hass: HomeAssistant) -> None:
    """Test that setting up a duplicate user aborts."""
    # First, create an existing entry
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    existing = MockConfigEntry(
        domain=DOMAIN,
        unique_id="user@example.com",
        data={"username": "user@example.com", "password": "old", "home_id": "h1"},
    )
    existing.add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})

    mock_auth = AsyncMock()
    mock_home = MagicMock()
    mock_home.id = "home_1"
    mock_home.name = "Home"
    mock_home.modules = []

    mock_account = AsyncMock()
    mock_account.homes = {"home_1": mock_home}

    with (
        patch(
            "custom_components.bticino_intercom.config_flow.validate_input",
            return_value=mock_auth,
        ),
        patch(
            "custom_components.bticino_intercom.config_flow.AsyncAccount",
            return_value=mock_account,
        ),
    ):
        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {"username": "user@example.com", "password": "test"},
        )

    assert result2["type"] is FlowResultType.ABORT
    assert result2["reason"] == "already_configured"


async def test_options_flow(
    hass: HomeAssistant,
    mock_setup_entry,
) -> None:
    """Test the options flow."""
    result = await hass.config_entries.options.async_init(mock_setup_entry.entry_id)

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "init"

    result2 = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input={"light_as_lock": True},
    )
    assert result2["type"] is FlowResultType.CREATE_ENTRY
    assert result2["data"]["light_as_lock"] is True
