"""Tests for BticinoSignalingClient (answer ack, stale-token retry, player/unmute)."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from custom_components.bticino_intercom.signaling_compat import BticinoSignalingClient


async def test_send_answer_with_ack_matches_correlation_id() -> None:
    """send_answer_with_ack resolves when the matching correlation_id ack arrives."""
    auth = MagicMock()
    client = BticinoSignalingClient(auth_handler=auth)
    client.set_session_from_push(
        session_id="sess-1",
        tag_id="tag-1",
        correlation_id="42",
        device_id="00:03:50:f2:4d:9f",
    )

    async def _fake_send_answer(sdp: str, session_id: str | None = None) -> None:
        asyncio.get_running_loop().call_soon(
            lambda: asyncio.create_task(client._handle_ack({"type": "ack", "status": "ok", "correlation_id": "42"}))
        )

    with patch("pybticino.SignalingClient.send_answer", side_effect=_fake_send_answer):
        ack = await client.send_answer_with_ack("v=0\r\n", session_id="sess-1", timeout=2.0)

    assert ack == {"type": "ack", "status": "ok", "correlation_id": "42"}


async def test_send_answer_with_ack_retries_once_on_invalid_token_code_2() -> None:
    """A code 2 (Invalid access_token) ack triggers disconnect, reconnect, and one retry."""
    auth = MagicMock()
    client = BticinoSignalingClient(auth_handler=auth)
    client.set_session_from_push(
        session_id="sess-2",
        tag_id="tag-2",
        correlation_id="77",
        device_id="00:03:50:f2:4d:9f",
    )

    client.disconnect = AsyncMock()
    client.connect = AsyncMock()

    attempts = 0

    async def _fake_send_answer(sdp: str, session_id: str | None = None) -> None:
        nonlocal attempts
        attempts += 1
        reply = (
            {"type": "ack", "status": "error", "correlation_id": "77", "error": {"code": 2, "message": "Invalid token"}}
            if attempts == 1
            else {"type": "ack", "status": "ok", "correlation_id": "77"}
        )
        asyncio.get_running_loop().call_soon(lambda r=reply: asyncio.create_task(client._handle_ack(r)))

    with patch("pybticino.SignalingClient.send_answer", side_effect=_fake_send_answer):
        ack = await client.send_answer_with_ack("v=0\r\n", session_id="sess-2", timeout=2.0)

    assert attempts == 2
    client.disconnect.assert_awaited_once()
    client.connect.assert_awaited_once()
    assert ack == {"type": "ack", "status": "ok", "correlation_id": "77"}


async def test_send_player_unmute_payload() -> None:
    """send_player('unmute') sends the RTC player message for the active session."""
    auth = MagicMock()
    client = BticinoSignalingClient(auth_handler=auth)
    client.set_session_from_push(
        session_id="sess-3",
        tag_id="tag-3",
        correlation_id="99",
        device_id="00:03:50:f2:4d:9f",
    )
    client._send = AsyncMock()

    await client.send_player("unmute", session_id="sess-3")

    client._send.assert_awaited_once()
    sent = client._send.call_args.args[0]
    assert sent["action"] == "rtc"
    assert sent["data"] == {"type": "player", "value": "unmute"}
    assert sent["session_id"] == "sess-3"
    assert sent["tag_id"] == "tag-3"
    assert sent["device_id"] == "00:03:50:f2:4d:9f"
