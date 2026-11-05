"""Tests for the call history websocket command.

The command is a public API: a card reads it to build the call log, so the
shape of what comes back is pinned here on purpose.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from homeassistant.core import HomeAssistant

from custom_components.bticino_intercom.const import DOMAIN
from custom_components.bticino_intercom.history import EventHistoryStore
from custom_components.bticino_intercom.websocket_api import async_register_websocket_api

COMMAND = f"{DOMAIN}/history"


def _event(event_id: str, module_id: str, timestamp: int, **extra) -> dict:
    """Build a stored record, with the fields the store always writes."""
    record = {
        "event_id": event_id,
        "module_id": module_id,
        "module_name": "Front Door" if module_id == "mod_a" else "Gate",
        "event_type": "incoming_call",
        "timestamp": timestamp,
        "ended_at": None,
        "answered": None,
        "snapshot_file": None,
        "vignette_file": None,
    }
    record.update(extra)
    return record


@pytest.fixture
async def seeded_history(hass: HomeAssistant, tmp_path):
    """Two entries with recorded calls: answered, missed and still open."""
    with patch.object(hass.config, "path", side_effect=lambda *parts: str(tmp_path.joinpath(*parts))):
        main = EventHistoryStore(hass, "entry_main")
        await main.async_load()
        mod_dir = main._root / "mod_a"
        mod_dir.mkdir(parents=True, exist_ok=True)
        (mod_dir / "evt_answered_snapshot.jpg").write_bytes(b"snap")
        (mod_dir / "evt_answered_vignette.jpg").write_bytes(b"vig")
        # newest first, the order the store keeps
        main._events = [
            _event("evt_open", "mod_a", 1700000300),
            _event("evt_missed", "mod_b", 1700000200, answered=False, event_type="terminated", ended_at=1700000260),
            _event(
                "evt_answered",
                "mod_a",
                1700000100,
                answered=True,
                event_type="answered_elsewhere",
                ended_at=1700000150,
                snapshot_file="mod_a/evt_answered_snapshot.jpg",
                vignette_file="mod_a/evt_answered_vignette.jpg",
            ),
        ]

        other = EventHistoryStore(hass, "entry_other")
        await other.async_load()
        other._events = [_event("evt_other", "mod_z", 1700000400)]

    hass.data.setdefault(DOMAIN, {})["entry_main"] = {"history": main}
    hass.data[DOMAIN]["entry_other"] = {"history": other}
    async_register_websocket_api(hass)
    yield
    hass.data.get(DOMAIN, {}).clear()


async def _history(client, **payload) -> list[dict]:
    """Send the command and return the events it answers with."""
    await client.send_json({"id": payload.pop("msg_id", 1), "type": COMMAND, **payload})
    message = await client.receive_json()
    assert message["success"], message
    return message["result"]["events"]


async def test_returns_every_event_newest_first(hass, hass_ws_client, seeded_history) -> None:
    """Records from every entry come back, most recent first."""
    events = await _history(await hass_ws_client(hass))
    assert [event["event_id"] for event in events] == [
        "evt_other",
        "evt_open",
        "evt_missed",
        "evt_answered",
    ]


async def test_event_carries_the_documented_fields(hass, hass_ws_client, seeded_history) -> None:
    """The shape a card reads must not drift."""
    events = await _history(await hass_ws_client(hass))
    answered = next(event for event in events if event["event_id"] == "evt_answered")
    assert answered["entry_id"] == "entry_main"
    assert answered["module_id"] == "mod_a"
    assert answered["module_name"] == "Front Door"
    assert answered["timestamp"] == 1700000100
    assert answered["ended_at"] == 1700000150
    assert answered["event_type"] == "answered_elsewhere"
    assert answered["answered"] is True
    # already signed, so a client can put the URL straight in an <img>
    assert answered["snapshot"].startswith("/api/bticino_intercom/image/entry_main/evt_answered/snapshot")
    assert answered["vignette"].startswith("/api/bticino_intercom/image/entry_main/evt_answered/vignette")
    assert "authSig=" in answered["snapshot"]
    assert "authSig=" in answered["vignette"]


async def test_answered_tells_missed_from_still_open(hass, hass_ws_client, seeded_history) -> None:
    """True once picked up, False once ended unanswered, None while open."""
    events = {event["event_id"]: event for event in await _history(await hass_ws_client(hass))}
    assert events["evt_answered"]["answered"] is True
    assert events["evt_missed"]["answered"] is False
    assert events["evt_open"]["answered"] is None


async def test_images_are_absent_when_nothing_was_stored(hass, hass_ws_client, seeded_history) -> None:
    """A record without files must not advertise an image URL."""
    events = {event["event_id"]: event for event in await _history(await hass_ws_client(hass))}
    assert "snapshot" not in events["evt_open"]
    assert "vignette" not in events["evt_open"]


async def test_entry_id_narrows_to_one_entry(hass, hass_ws_client, seeded_history) -> None:
    """Asking for one entry leaves the other one out."""
    events = await _history(await hass_ws_client(hass), entry_id="entry_main")
    assert {event["entry_id"] for event in events} == {"entry_main"}
    assert len(events) == 3


async def test_module_id_narrows_to_one_module(hass, hass_ws_client, seeded_history) -> None:
    """Asking for one module leaves the other modules out."""
    events = await _history(await hass_ws_client(hass), module_id="mod_a")
    assert [event["event_id"] for event in events] == ["evt_open", "evt_answered"]


async def test_limit_caps_the_answer(hass, hass_ws_client, seeded_history) -> None:
    """The limit applies to what comes back, newest kept."""
    events = await _history(await hass_ws_client(hass), limit=2)
    assert [event["event_id"] for event in events] == ["evt_other", "evt_open"]


async def test_limit_is_bounded(hass, hass_ws_client, seeded_history) -> None:
    """A limit outside the allowed range is refused rather than clamped."""
    client = await hass_ws_client(hass)
    await client.send_json({"id": 1, "type": COMMAND, "limit": 0})
    message = await client.receive_json()
    assert not message["success"]
    await client.send_json({"id": 2, "type": COMMAND, "limit": 500})
    message = await client.receive_json()
    assert not message["success"]


async def test_entry_without_a_store_is_skipped(hass, hass_ws_client, seeded_history) -> None:
    """An entry still loading has no history store, and must not break the call."""
    hass.data[DOMAIN]["entry_loading"] = {"coordinator": object()}
    events = await _history(await hass_ws_client(hass))
    assert [event["event_id"] for event in events] == [
        "evt_other",
        "evt_open",
        "evt_missed",
        "evt_answered",
    ]
