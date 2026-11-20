"""SignalingClient subclass backporting pybticino PR #32 and PR #33.

Allows testing answer-ack matching, stale-token reconnect retry, and
player/unmute on top of standard pybticino 1.10.1 without requiring a custom
library wheel inside Home Assistant.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from pybticino import AuthHandler, SignalingClient
from pybticino.exceptions import PyBticinoException

_LOGGER = logging.getLogger(__name__)

ANSWER_ACK_TIMEOUT = 10.0


class BticinoSignalingClient(SignalingClient):
    """SignalingClient with answer-ack waiting, stale-token retry, and send_player."""

    def __init__(self, auth_handler: AuthHandler) -> None:
        super().__init__(auth_handler=auth_handler)
        self._pending_answer_acks: dict[str, asyncio.Future[dict[str, Any]]] = {}

    def _fail_pending_answers(self, reason: str) -> None:
        for fut in self._pending_answer_acks.values():
            if not fut.done():
                fut.set_exception(PyBticinoException(reason))
        self._pending_answer_acks.clear()

    async def disconnect(self) -> None:
        await super().disconnect()
        self._fail_pending_answers("Signaling disconnected before the answer was acked")

    async def _listen(self) -> None:
        try:
            await super()._listen()
        finally:
            self._fail_pending_answers("Signaling socket closed before the answer was acked")

    async def _handle_ack(self, message: dict[str, Any]) -> None:
        corr = message.get("correlation_id")
        if corr is not None:
            corr_str = str(corr)
            if corr_str in self._pending_answer_acks:
                fut = self._pending_answer_acks.pop(corr_str)
                if not fut.done():
                    fut.set_result(message)
                return
        await super()._handle_ack(message)

    async def send_terminate(self, session_id: str | None = None) -> None:
        resolved = self._resolve_session(session_id)
        if resolved is not None:
            sid, session = resolved
            if session.correlation_id:
                fut = self._pending_answer_acks.pop(str(session.correlation_id), None)
                if fut is not None and not fut.done():
                    fut.set_exception(PyBticinoException(f"Session {sid} was terminated before the answer was acked"))
        await super().send_terminate(session_id=session_id)

    async def ensure_ready(self) -> None:
        """Ensure signaling is connected; only resubscribe if reusing an older socket."""
        ws_before = self._websocket if self.is_connected else None
        await self.ensure_connected()
        if ws_before is not None and self._websocket is ws_before:
            await self.resubscribe()
            # Give the server a brief moment to process the subscribe before an
            # offer/answer goes out (mitigates Invalid access_token race).
            await asyncio.sleep(0.1)

    async def send_answer_with_ack(
        self,
        sdp: str,
        session_id: str | None = None,
        *,
        timeout: float = ANSWER_ACK_TIMEOUT,
    ) -> dict[str, Any] | None:
        """Send an SDP answer and await the ack matching the offer's correlation_id.

        If the cloud refuses with code 2 (Invalid access_token), disconnects,
        reconnects with a fresh token, restores the push session, and retries once.
        """
        ack = await self._send_answer_once(sdp, session_id=session_id, timeout=timeout)
        if not ack:
            return ack

        error = ack.get("error") if isinstance(ack.get("error"), dict) else None
        if error and error.get("code") == 2:
            resolved = self._resolve_session(session_id)
            if resolved is None:
                return ack
            sid, session = resolved
            tag_id = session.tag_id
            corr_id = session.correlation_id
            device_id = session.device_id
            _LOGGER.warning(
                "Answer for session %s rejected with Invalid access_token (code 2); reconnecting and retrying",
                sid,
            )
            await self.disconnect()
            await self.connect()
            self.set_session_from_push(
                session_id=sid,
                tag_id=tag_id,
                correlation_id="" if corr_id is None else str(corr_id),
                device_id=device_id,
            )
            ack = await self._send_answer_once(sdp, session_id=sid, timeout=timeout)
        return ack

    async def _send_answer_once(
        self,
        sdp: str,
        session_id: str | None,
        timeout: float,
    ) -> dict[str, Any] | None:
        resolved = self._resolve_session(session_id)
        if resolved is None:
            raise PyBticinoException("No active session to send answer to")
        sid, session = resolved
        corr_id = str(session.correlation_id) if session.correlation_id else None

        if corr_id is None:
            await super().send_answer(sdp, session_id=sid)
            return None

        if corr_id in self._pending_answer_acks:
            raise PyBticinoException(f"An answer to session {sid} is already waiting for its ack")

        loop = asyncio.get_running_loop()
        ack_future: asyncio.Future[dict[str, Any]] = loop.create_future()
        self._pending_answer_acks[corr_id] = ack_future
        try:
            await super().send_answer(sdp, session_id=sid)
            ack = await asyncio.wait_for(ack_future, timeout=timeout)
        except TimeoutError as err:
            raise PyBticinoException(f"Timed out waiting for answer ack (session={sid})") from err
        finally:
            if self._pending_answer_acks.get(corr_id) is ack_future:
                del self._pending_answer_acks[corr_id]

        if ack.get("error"):
            _LOGGER.warning("Answer to session %s refused: %s", sid, ack["error"])
        else:
            _LOGGER.info("Answer to session %s acked (correlation_id=%s)", sid, corr_id)
        return ack

    async def send_player(self, value: str, session_id: str | None = None) -> None:
        """Send a player command (e.g. 'unmute') on an answered call session."""
        if hasattr(super(), "send_player"):
            await super().send_player(value, session_id=session_id)  # type: ignore[misc]
            return

        resolved = self._resolve_session(session_id)
        if resolved is None:
            raise PyBticinoException("No active session to send a player command to")
        sid, session = resolved

        message = {
            "action": "rtc",
            "data": {"type": "player", "value": value},
            "session_id": sid,
            "tag_id": session.tag_id,
            "device_id": session.device_id,
            "correlation_id": str(next(self._correlation_ids)),
        }
        await self._send(message)
        _LOGGER.info("Player %r sent for session %s", value, sid)
