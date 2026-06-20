# WebRTC: Answer Incoming Calls Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix WebRTC camera to answer the device's incoming call offer instead of sending a conflicting new offer, eliminating the "Max number of peers reached" error.

**Architecture:** When a doorbell rings, the BTicino device sends an RTC offer via the push WebSocket. The coordinator stores it in `_active_call` (including `session_id`, `tag_id`, `correlation_id`, `device_id`, `sdp`). When the browser opens the camera, instead of calling `signaling.send_offer()` (which creates a NEW call), the camera must: (1) initialize the signaling session from the push event via `set_session_from_push()`, (2) modify the browser's SDP offer to be compatible as an answer (change DTLS `a=setup:actpass` → `a=setup:active`), and (3) send it to the device via `signaling.send_answer()`. When no incoming call is active, the camera falls back to the current `send_offer()` flow for on-demand viewing.

**Tech Stack:** pybticino `SignalingClient` (`send_answer`, `set_session_from_push`), HA WebRTC framework (`async_handle_async_webrtc_offer`, `WebRTCAnswer`), SDP string manipulation.

---

### File Structure

| File | Action | Responsibility |
|------|--------|----------------|
| `custom_components/bticino_intercom/camera.py` | Modify | Add incoming-call-aware WebRTC handling in `BticinoWebRTCCamera` |
| `custom_components/bticino_intercom/coordinator.py` | Modify (minor) | Wire `set_session_from_push()` on signaling client when offer arrives |
| `custom_components/bticino_intercom/__init__.py` | Modify (minor) | Pass signaling_client to coordinator so it can call `set_session_from_push` |
| `tests/test_coordinator.py` | Modify | Test that offer wires signaling session |
| `tests/test_camera.py` | Modify | Test answer-mode vs offer-mode branching |

---

### Task 1: Wire signaling session from push offer in coordinator

When the coordinator processes an RTC offer (`rtc_action == "offer"`), it should call `signaling.set_session_from_push()` so the SignalingClient is ready for `send_answer()`.

**Files:**
- Modify: `custom_components/bticino_intercom/__init__.py:112-119` — pass signaling_client to coordinator
- Modify: `custom_components/bticino_intercom/coordinator.py:45-55` — accept signaling_client, call `set_session_from_push` on offer
- Test: `tests/test_coordinator.py`

- [ ] **Step 1: Write the failing test**

In `tests/test_coordinator.py`, add a test that verifies the signaling client's session is set when an RTC offer arrives:

```python
async def test_rtc_offer_sets_signaling_session(
    hass: HomeAssistant,
    mock_setup_entry: MockConfigEntry,
) -> None:
    """Test that RTC offer wires the signaling session for send_answer."""
    coordinator = hass.data[DOMAIN][mock_setup_entry.entry_id]["coordinator"]
    signaling = hass.data[DOMAIN][mock_setup_entry.entry_id]["signaling_client"]

    message = {
        "push_type": "BNC1-rtc",
        "extra_params": {
            "session_id": "sess_offer_1",
            "tag_id": "tag_123",
            "correlation_id": "corr_456",
            "device_id": BRIDGE_MAC,
            "data": {
                "type": "offer",
                "session_description": {
                    "type": "call",
                    "sdp": "v=0\r\no=- 123 2 IN IP4 0.0.0.0\r\n...",
                    "module_id": EXT_UNIT_MODULE_ID,
                },
            },
        },
    }

    coordinator._process_websocket_event(message)
    await hass.async_block_till_done()

    # Verify signaling session was set from push
    assert signaling.session_id == "sess_offer_1"
    assert signaling._tag_id == "tag_123"
    assert signaling._device_id == BRIDGE_MAC
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_coordinator.py::test_rtc_offer_sets_signaling_session -v`
Expected: FAIL — coordinator doesn't have access to signaling_client yet.

- [ ] **Step 3: Pass signaling_client to coordinator**

In `__init__.py`, pass the signaling client to the coordinator:

```python
# __init__.py line ~100
coordinator = BticinoIntercomCoordinator(
    hass, entry, account, None, signaling_client=signaling_client,
)
```

But `signaling_client` is created at line 112, after the coordinator. Reorder:

```python
# Create signaling client first (lazy-connected on first use)
signaling_client = SignalingClient(auth_handler=auth_handler)

# Create coordinator with signaling client reference
coordinator = BticinoIntercomCoordinator(
    hass, entry, account, None, signaling_client=signaling_client,
)
```

In `coordinator.py`, accept and store it:

```python
def __init__(
    self,
    hass: HomeAssistant,
    entry: ConfigEntry,
    account: AsyncAccount,
    websocket_client: WebsocketClient,
    signaling_client: SignalingClient | None = None,
) -> None:
    # ... existing init ...
    self._signaling_client = signaling_client
```

- [ ] **Step 4: Call set_session_from_push on RTC offer**

In `coordinator.py`, in `_process_rtc_event` when `rtc_action == "offer"`, add after storing `_active_call`:

```python
# Prepare signaling client for answering this call
if self._signaling_client:
    self._signaling_client.set_session_from_push(
        session_id=session_id,
        tag_id=extra_params.get("tag_id", ""),
        correlation_id=str(extra_params.get("correlation_id", "")),
        device_id=device_id,
    )
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/test_coordinator.py::test_rtc_offer_sets_signaling_session -v`
Expected: PASS

- [ ] **Step 6: Run full test suite for regressions**

Run: `pytest tests/ -v`
Expected: All tests pass (existing `mock_setup_entry` fixture needs signaling_client wired in conftest).

- [ ] **Step 7: Commit**

```bash
git add custom_components/bticino_intercom/__init__.py custom_components/bticino_intercom/coordinator.py tests/test_coordinator.py
git commit -m "feat: wire signaling session from push offer for incoming call answering"
```

---

### Task 2: Add SDP offer-to-answer conversion helper

The browser's SDP offer needs minimal modifications to be sent as an answer to the device: change DTLS role from `actpass` to `active`.

**Files:**
- Modify: `custom_components/bticino_intercom/camera.py` — add `_convert_offer_to_answer_sdp()` static method
- Test: `tests/test_camera.py`

- [ ] **Step 1: Write the failing test**

```python
from custom_components.bticino_intercom.camera import BticinoWebRTCCamera


class TestSdpConversion:
    """Test SDP offer-to-answer conversion."""

    def test_actpass_becomes_active(self) -> None:
        """DTLS setup role should change from actpass to active."""
        offer_sdp = (
            "v=0\r\n"
            "o=- 123 2 IN IP4 0.0.0.0\r\n"
            "m=video 9 UDP/TLS/RTP/SAVPF 96\r\n"
            "a=setup:actpass\r\n"
            "a=mid:0\r\n"
        )
        answer_sdp = BticinoWebRTCCamera.convert_offer_to_answer_sdp(offer_sdp)
        assert "a=setup:active\r\n" in answer_sdp
        assert "a=setup:actpass" not in answer_sdp

    def test_preserves_other_attributes(self) -> None:
        """Other SDP attributes should be preserved unchanged."""
        offer_sdp = (
            "v=0\r\n"
            "o=- 123 2 IN IP4 0.0.0.0\r\n"
            "a=ice-ufrag:abcd\r\n"
            "a=ice-pwd:secret123\r\n"
            "a=setup:actpass\r\n"
            "a=fingerprint:sha-256 AA:BB:CC\r\n"
        )
        answer_sdp = BticinoWebRTCCamera.convert_offer_to_answer_sdp(offer_sdp)
        assert "a=ice-ufrag:abcd\r\n" in answer_sdp
        assert "a=ice-pwd:secret123\r\n" in answer_sdp
        assert "a=fingerprint:sha-256 AA:BB:CC\r\n" in answer_sdp

    def test_multiple_media_sections(self) -> None:
        """All m-sections should have setup converted."""
        offer_sdp = (
            "v=0\r\n"
            "m=audio 9 UDP/TLS/RTP/SAVPF 111\r\n"
            "a=setup:actpass\r\n"
            "m=video 9 UDP/TLS/RTP/SAVPF 96\r\n"
            "a=setup:actpass\r\n"
        )
        answer_sdp = BticinoWebRTCCamera.convert_offer_to_answer_sdp(offer_sdp)
        assert answer_sdp.count("a=setup:active") == 2
        assert "a=setup:actpass" not in answer_sdp
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_camera.py::TestSdpConversion -v`
Expected: FAIL — `convert_offer_to_answer_sdp` not defined.

- [ ] **Step 3: Implement the SDP conversion**

In `camera.py`, add as a static method on `BticinoWebRTCCamera`:

```python
@staticmethod
def convert_offer_to_answer_sdp(offer_sdp: str) -> str:
    """Convert a browser SDP offer to be usable as an answer.

    The main change is the DTLS setup role: offers use 'actpass'
    (willing to be either active or passive) while answers must
    choose 'active' (the answerer initiates the DTLS handshake).
    """
    return offer_sdp.replace("a=setup:actpass", "a=setup:active")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_camera.py::TestSdpConversion -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add custom_components/bticino_intercom/camera.py tests/test_camera.py
git commit -m "feat: add SDP offer-to-answer conversion for incoming calls"
```

---

### Task 3: Implement answer-mode in WebRTC camera

Modify `async_handle_async_webrtc_offer()` to detect active incoming calls and use `send_answer()` instead of `send_offer()`.

**Files:**
- Modify: `custom_components/bticino_intercom/camera.py:332-407` — branch on `coordinator.active_call`
- Test: `tests/test_camera.py`

- [ ] **Step 1: Write the failing test for answer mode**

```python
class TestWebRTCAnswerMode:
    """Test WebRTC camera answering incoming calls."""

    @pytest.fixture
    def coordinator(self, hass, mock_setup_entry):
        return hass.data[DOMAIN][mock_setup_entry.entry_id]["coordinator"]

    @pytest.fixture
    def signaling(self, hass, mock_setup_entry):
        return hass.data[DOMAIN][mock_setup_entry.entry_id]["signaling_client"]

    async def test_answer_mode_when_active_call(self, coordinator, signaling) -> None:
        """When there's an active incoming call, camera should send_answer not send_offer."""
        # Simulate an active incoming call
        coordinator._active_call = {
            "session_id": "sess_1",
            "tag_id": "tag_1",
            "correlation_id": "corr_1",
            "device_id": "00:03:50:AA:BB:CC",
            "module_id": "ext_unit_1",
            "sdp": "v=0\r\ndevice offer sdp\r\n",
        }

        # Track which signaling method was called
        signaling.send_answer = AsyncMock()
        signaling.send_offer = AsyncMock(return_value="sess_new")
        signaling._is_connected = True
        signaling.connect = AsyncMock()

        messages = []
        def send_message(msg):
            messages.append(msg)

        camera = None
        for entity in hass.data["entity_components"]["camera"].entities:
            if "live_video" in entity.entity_id:
                camera = entity
                break

        browser_offer = "v=0\r\na=setup:actpass\r\nm=video 9 UDP/TLS/RTP/SAVPF 96\r\n"
        await camera.async_handle_async_webrtc_offer(browser_offer, "browser_sess", send_message)

        # Should have called send_answer, NOT send_offer
        signaling.send_answer.assert_called_once()
        signaling.send_offer.assert_not_called()

        # The SDP sent should have setup:active (converted from actpass)
        sent_sdp = signaling.send_answer.call_args[0][0]
        assert "a=setup:active" in sent_sdp
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_camera.py::TestWebRTCAnswerMode::test_answer_mode_when_active_call -v`
Expected: FAIL — camera always calls `send_offer`.

- [ ] **Step 3: Implement answer-mode branching**

Replace the core of `async_handle_async_webrtc_offer()` in `camera.py`:

```python
async def async_handle_async_webrtc_offer(
    self,
    offer_sdp: str,
    session_id: str,
    send_message: WebRTCSendMessage,
) -> None:
    """Handle a WebRTC offer from the HA frontend.

    Two modes:
    - Answer mode (active call): The device already sent an offer via
      the push WebSocket. We convert the browser's SDP to an answer
      and send it to the device via send_answer(). The device then
      sends its own answer back, which we forward to the browser.
    - Offer mode (no active call): We forward the browser's offer
      to the device via send_offer() for on-demand viewing.
    """
    device_id = self.coordinator.main_device_id
    if not device_id:
        send_message(WebRTCError(code="no_device", message="No bridge device found"))
        return

    self._session_ready = False
    self._pending_candidates.clear()

    try:
        # Ensure signaling is connected
        if not self._signaling.is_connected:
            await self._signaling.connect()

        # Set up callbacks for this session
        async def on_answer(sig_session_id: str, sdp: str) -> None:
            _LOGGER.info("Received answer SDP for session %s", sig_session_id)
            send_message(WebRTCAnswer(answer=sdp))

        async def on_candidate(sig_session_id: str, ice: dict) -> None:
            candidate_str = ice.get("candidate", "")
            sdp_m_line_index = ice.get("sdp_m_line_index", ice.get("sdpMLineIndex", 0))
            _LOGGER.debug("Received remote ICE candidate (m=%d)", sdp_m_line_index)
            send_message(
                WebRTCCandidate(
                    RTCIceCandidateInit(
                        candidate=candidate_str,
                        sdp_m_line_index=sdp_m_line_index,
                    )
                )
            )

        async def on_event(sig_session_id: str, event_type: str, data: dict) -> None:
            error = data.get("data", {}).get("error", {})
            error_msg = error.get("message", event_type) if error else event_type
            _LOGGER.warning("Signaling event %s: %s", event_type, error_msg)
            send_message(WebRTCError(code=event_type, message=error_msg))

        self._signaling._on_answer = on_answer
        self._signaling._on_candidate = on_candidate
        self._signaling._on_event = on_event

        active_call = self.coordinator.active_call
        if active_call and active_call.get("sdp"):
            # --- Answer mode: respond to the device's incoming call ---
            _LOGGER.info(
                "Answering incoming call (session=%s, device=%s)",
                active_call.get("session_id"),
                device_id,
            )
            answer_sdp = self.convert_offer_to_answer_sdp(offer_sdp)
            await self._signaling.send_answer(answer_sdp)
        else:
            # --- Offer mode: initiate on-demand call ---
            module_id = None
            for mid, mdata in self.coordinator.data.get("modules", {}).items():
                variant = mdata.get("variant", "")
                if "bneu_external_unit" in variant:
                    module_id = mid
                    break

            _LOGGER.info("Sending WebRTC offer to device %s (module=%s)", device_id, module_id)
            await self._signaling.send_offer(
                device_id=device_id,
                sdp=offer_sdp,
                module_id=module_id,
            )

        # Session is now ready — flush any buffered ICE candidates
        self._session_ready = True
        await self._flush_pending_candidates()

    except Exception as err:
        _LOGGER.exception("Failed to handle WebRTC offer")
        send_message(WebRTCError(code="offer_failed", message=str(err)))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_camera.py::TestWebRTCAnswerMode -v`
Expected: PASS

- [ ] **Step 5: Write test for offer-mode fallback (no active call)**

```python
    async def test_offer_mode_when_no_active_call(self, coordinator, signaling) -> None:
        """When no active call, camera should use send_offer (on-demand viewing)."""
        coordinator._active_call = None

        signaling.send_answer = AsyncMock()
        signaling.send_offer = AsyncMock(return_value="sess_new")
        signaling._is_connected = True
        signaling.connect = AsyncMock()

        messages = []
        def send_message(msg):
            messages.append(msg)

        camera = None
        for entity in hass.data["entity_components"]["camera"].entities:
            if "live_video" in entity.entity_id:
                camera = entity
                break

        browser_offer = "v=0\r\na=setup:actpass\r\nm=video 9 UDP/TLS/RTP/SAVPF 96\r\n"
        await camera.async_handle_async_webrtc_offer(browser_offer, "browser_sess", send_message)

        signaling.send_offer.assert_called_once()
        signaling.send_answer.assert_not_called()
```

- [ ] **Step 6: Run full test suite**

Run: `pytest tests/ -v`
Expected: All tests pass.

- [ ] **Step 7: Commit**

```bash
git add custom_components/bticino_intercom/camera.py tests/test_camera.py
git commit -m "feat: answer incoming calls via send_answer instead of send_offer

When the doorbell rings (active_call with SDP), the camera now answers
the device's existing call via send_answer() instead of trying to create
a new call with send_offer(). This fixes 'Max number of peers reached'.

Falls back to send_offer() for on-demand viewing when no call is active."
```

---

### Task 4: End-to-end test with real device

This task cannot be unit-tested — it requires ringing the physical doorbell.

**Files:**
- Use: `/tmp/webrtc_test.py` (already written)

- [ ] **Step 1: Bump version to v2.0.0-rc2**

```bash
# In manifest.json, change version to "2.0.0-rc2"
```

- [ ] **Step 2: Deploy to HA via Syncthing**

The code is in `/home/koma/.syncthing/homeassistant_configs/custom_components/bticino_intercom/`. Syncthing auto-syncs. After sync, restart HA:

```bash
ssh root@ha.asgard.lan -p 22222 "ha core restart"
```

- [ ] **Step 3: Ring doorbell and run test script**

```bash
python3 /tmp/webrtc_test.py
```

Expected: WebRTC session established, video recorded to `/tmp/bticino_webrtc_test.mp4`.

- [ ] **Step 4: Verify recording**

```bash
ffprobe /tmp/bticino_webrtc_test.mp4
```

- [ ] **Step 5: Tag and release RC**

```bash
git tag v2.0.0-rc2
git push origin dev/webrtc v2.0.0-rc2
```
