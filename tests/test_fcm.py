"""Tests for the native Android FCM push receiver."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.bticino_intercom.const import DOMAIN, ENV_FCM_API_KEY, OPT_FCM_API_KEY
from custom_components.bticino_intercom.fcm import (
    FIREBASE_APP_ID,
    FIREBASE_MESSAGING_SENDER_ID,
    FIREBASE_PROJECT_ID,
    BticinoFcmReceiver,
    _SafeAndroidFcmClient,
    async_drop_fcm_registration,
    build_firebase_config,
    resolve_fcm_api_key,
)


def test_safe_fcm_client_extracts_app_data_and_skips_deleted_messages() -> None:
    """Native Android pushes arrive in app_data; deleted_messages must be ignored."""
    received: list[tuple[dict[str, str], str, object]] = []
    client = _SafeAndroidFcmClient.__new__(_SafeAndroidFcmClient)
    client.callback = lambda app_map, pid, ctx: received.append((app_map, pid, ctx))
    client.callback_context = "ctx"

    deleted_msg = SimpleNamespace(
        app_data=[SimpleNamespace(key="message_type", value="deleted_messages")],
        persistent_id="pid-0",
    )
    client._handle_data_message(deleted_msg)
    assert received == []

    offer_msg = SimpleNamespace(
        app_data=[
            SimpleNamespace(key="push_type", value="BNC1-rtc"),
            SimpleNamespace(key="extra_params", value='{"session_id": "s1"}'),
        ],
        persistent_id="pid-1",
    )
    client._handle_data_message(offer_msg)
    assert received == [({"push_type": "BNC1-rtc", "extra_params": '{"session_id": "s1"}'}, "pid-1", "ctx")]


async def test_safe_fcm_client_reset_terminates_immediately() -> None:
    """_reset must call _terminate instead of spinning on a failed StreamReader."""
    client = _SafeAndroidFcmClient.__new__(_SafeAndroidFcmClient)
    client._terminate = MagicMock()

    await client._reset()

    client._terminate.assert_called_once_with()


async def test_fcm_receiver_forwards_rtc_and_ignores_non_rtc(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Only *-rtc pushes are forwarded into the coordinator with source='fcm'."""
    forwarded: list[dict] = []

    async def _cb(msg: dict) -> None:
        forwarded.append(msg)

    receiver = BticinoFcmReceiver(
        hass=hass,
        entry=mock_config_entry,
        auth_handler=MagicMock(),
        message_callback=_cb,
    )

    # Non-RTC status push is handled by the Netatmo WebSocket and ignored here.
    receiver._on_fcm_message({"push_type": "BNC1-incoming_call", "extra_params": "{}"}, "pid-1", None)
    await hass.async_block_till_done()
    assert forwarded == []

    # Invalid JSON extra_params is safely skipped.
    receiver._on_fcm_message({"push_type": "BNC1-rtc", "extra_params": "{not-json"}, "pid-2", None)
    await hass.async_block_till_done()
    assert forwarded == []

    # Valid RTC offer push is normalized and forwarded.
    extra = {"session_id": "sess-1", "data": {"type": "offer"}}
    receiver._on_fcm_message(
        {
            "push_type": "BNC1-rtc",
            "extra_params": json.dumps(extra),
            "alert": "Incoming call",
            "category": "rtc",
        },
        "pid-3",
        None,
    )
    await hass.async_block_till_done()

    assert len(forwarded) == 1
    assert forwarded[0] == {
        "type": "FCM",
        "source": "fcm",
        "push_type": "BNC1-rtc",
        "extra_params": extra,
        "alert": "Incoming call",
        "category": "rtc",
    }


async def test_async_drop_fcm_registration_drops_context_and_removes_store(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Removing a config entry drops the Netatmo push context and wipes stored credentials."""
    auth_handler = MagicMock()
    with (
        patch("custom_components.bticino_intercom.fcm.Store") as mock_store_cls,
        patch("custom_components.bticino_intercom.fcm._post_netatmo_push", new_callable=AsyncMock) as mock_post,
    ):
        mock_store_cls.return_value.async_load = AsyncMock(return_value={"fcm_token": "tok-123"})
        mock_store_cls.return_value.async_remove = AsyncMock()

        await async_drop_fcm_registration(hass, mock_config_entry, auth_handler)

    mock_post.assert_awaited_once()
    payload = mock_post.call_args.args[3]
    assert json.loads(payload["credentials"]) == {"device_token": "tok-123"}
    assert payload["type"] == "Android"
    mock_store_cls.return_value.async_remove.assert_awaited_once()
    assert mock_store_cls.call_args.args[2] == f"{DOMAIN}.fcm.{mock_config_entry.entry_id}"


def test_resolve_fcm_api_key_prefers_option_over_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Entry option fcm_api_key takes precedence over BTICINO_FCM_API_KEY env var."""
    monkeypatch.delenv(ENV_FCM_API_KEY, raising=False)
    empty_entry = MockConfigEntry(domain=DOMAIN, data={})
    assert resolve_fcm_api_key(empty_entry) == ""

    monkeypatch.setenv(ENV_FCM_API_KEY, "  env-firebase-key  ")
    assert resolve_fcm_api_key(empty_entry) == "env-firebase-key"

    opt_entry = MockConfigEntry(domain=DOMAIN, data={}, options={OPT_FCM_API_KEY: "  opt-firebase-key  "})
    assert resolve_fcm_api_key(opt_entry) == "opt-firebase-key"

    cfg = build_firebase_config("opt-firebase-key")
    assert cfg.api_key == "opt-firebase-key"
    assert cfg.project_id == FIREBASE_PROJECT_ID
    assert cfg.app_id == FIREBASE_APP_ID
    assert cfg.messaging_sender_id == FIREBASE_MESSAGING_SENDER_ID


async def test_fcm_receiver_disabled_without_api_key(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without an API key in options or env, the receiver stays disabled and exits cleanly."""
    monkeypatch.delenv(ENV_FCM_API_KEY, raising=False)

    async def _cb(msg: dict) -> None:
        pass

    receiver = BticinoFcmReceiver(
        hass=hass,
        entry=mock_config_entry,
        auth_handler=MagicMock(),
        message_callback=_cb,
    )
    assert receiver.enabled is False

    with patch.object(receiver, "_ensure_registered", new_callable=AsyncMock) as mock_reg:
        await receiver.run_forever()
    mock_reg.assert_not_awaited()

    monkeypatch.setenv(ENV_FCM_API_KEY, "env-firebase-key")
    enabled_receiver = BticinoFcmReceiver(
        hass=hass,
        entry=mock_config_entry,
        auth_handler=MagicMock(),
        message_callback=_cb,
    )
    assert enabled_receiver.enabled is True
