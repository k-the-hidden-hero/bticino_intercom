"""WebSocket API for the BTicino Intercom call history.

The Lovelace card needs the call log as data - who called, when, and whether
the call was answered - which a Media Source listing cannot carry, so it is
served here instead. Image URLs come back already signed, so the card can show
them without a round trip per thumbnail.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import TYPE_CHECKING, Any

import voluptuous as vol
from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant, callback

from .const import DOMAIN
from .history import IMAGE_KINDS, EventHistoryStore
from .media_source import BticinoHistoryImageView

if TYPE_CHECKING:
    from homeassistant.components.websocket_api.connection import ActiveConnection

_LOGGER = logging.getLogger(__name__)

DEFAULT_LIMIT = 50
MAX_LIMIT = 200
SIGNED_URL_LIFETIME = timedelta(hours=1)
WEBSOCKET_API_FLAG = f"{DOMAIN}_websocket_api_registered"


@callback
def async_register_websocket_api(hass: HomeAssistant) -> None:
    """Register the history command, once per Home Assistant instance."""
    if hass.data.get(WEBSOCKET_API_FLAG):
        return
    websocket_api.async_register_command(hass, ws_list_history)
    hass.data[WEBSOCKET_API_FLAG] = True


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/history",
        vol.Optional("entry_id"): str,
        vol.Optional("module_id"): str,
        vol.Optional("limit", default=DEFAULT_LIMIT): vol.All(int, vol.Range(min=1, max=MAX_LIMIT)),
    }
)
@callback
def ws_list_history(
    hass: HomeAssistant,
    connection: ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Send the recorded calls, most recent first."""
    limit: int = msg["limit"]
    wanted_entry: str | None = msg.get("entry_id")
    module_id: str | None = msg.get("module_id")

    events: list[dict[str, Any]] = []
    for entry_id, entry_data in hass.data.get(DOMAIN, {}).items():
        if not isinstance(entry_data, dict):
            continue
        if wanted_entry and entry_id != wanted_entry:
            continue
        store: EventHistoryStore | None = entry_data.get("history")
        if store is None:
            continue
        events.extend(
            _as_payload(hass, connection, entry_id, event)
            for event in store.list_events(limit=limit, module_id=module_id)
        )

    events.sort(key=lambda event: event.get("timestamp") or 0, reverse=True)
    connection.send_result(msg["id"], {"events": events[:limit]})


def _as_payload(
    hass: HomeAssistant,
    connection: ActiveConnection,
    entry_id: str,
    event: dict[str, Any],
) -> dict[str, Any]:
    """Turn a stored record into what the card needs."""
    event_id = event.get("event_id")
    payload: dict[str, Any] = {
        "entry_id": entry_id,
        "event_id": event_id,
        "module_id": event.get("module_id"),
        "module_name": event.get("module_name"),
        "timestamp": event.get("timestamp"),
        "ended_at": event.get("ended_at"),
        "event_type": event.get("event_type"),
        # True once the call was picked up elsewhere, False once it ended
        # unanswered, None while it is still open
        "answered": event.get("answered"),
    }
    for kind in IMAGE_KINDS:
        if not event_id or not event.get(f"{kind}_file"):
            continue
        payload[kind] = _signed(hass, connection, BticinoHistoryImageView.build_url(entry_id, event_id, kind))
    return payload


def _signed(hass: HomeAssistant, connection: ActiveConnection, path: str) -> str:
    """Return a path the browser can fetch without an auth header."""
    try:
        from homeassistant.components.http.auth import async_sign_path

        return async_sign_path(
            hass,
            path,
            SIGNED_URL_LIFETIME,
            refresh_token_id=connection.refresh_token_id,
        )
    except Exception:  # the image is simply not shown if signing is unavailable
        _LOGGER.debug("Could not sign %s", path, exc_info=True)
        return path
