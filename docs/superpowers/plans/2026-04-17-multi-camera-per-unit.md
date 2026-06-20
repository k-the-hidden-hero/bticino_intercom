# Multi-Camera Per External Unit Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Create one WebRTC camera entity per external unit (BNEU module), each targeting its specific module_id, instead of one global camera that picks the first unit.

**Architecture:** In `async_setup_entry`, iterate over all BNEU modules in coordinator data and create a `BticinoWebRTCCamera` for each. Each camera stores its own `module_id` and uses it directly in `send_offer()`. In answer mode, the camera only answers calls whose `active_call["module_id"]` matches its own. Entity naming uses the module's name from topology (e.g., "Citofono Strada").

**Tech Stack:** HA camera platform, pybticino `SignalingClient.send_offer(module_id=...)`, existing WebRTC infrastructure.

---

## File Structure

| File | Action | Change |
|------|--------|--------|
| `custom_components/bticino_intercom/camera.py` | Modify | `async_setup_entry`: loop BNEU modules, create one camera each. `BticinoWebRTCCamera.__init__`: accept `module_id` + `module_name`. Offer mode: use `self._module_id` directly. Answer mode: match `active_call["module_id"]`. |
| `tests/conftest.py` | Modify | Add second BNEU module to `mock_modules_data`. |
| `tests/test_webrtc_camera.py` | Modify | Update count assertions (2 cameras), add per-module tests. |

---

### Task 1: Add second external unit to test fixtures

**Files:**
- Modify: `tests/conftest.py`

- [ ] **Step 1: Add constants for second external unit**

In `tests/conftest.py`, after the existing constants (line ~23), add:

```python
EXTERNAL_UNIT_2_ID = "d9a63b-a16f-2ef633a2f85f"
EXTERNAL_UNIT_2_NAME = "Citofono Ingresso"
```

- [ ] **Step 2: Add second BNEU module to mock_modules_data fixture**

In `mock_modules_data()`, after the existing `EXTERNAL_UNIT_ID` entry (after line ~69), add:

```python
EXTERNAL_UNIT_2_ID: {
    "id": EXTERNAL_UNIT_2_ID,
    "type": "BNEU",
    "name": EXTERNAL_UNIT_2_NAME,
    "reachable": True,
    "bridge": BRIDGE_MAC,
    "variant": "BNEU:bneu_external_unit",
},
```

- [ ] **Step 3: Run existing tests to verify no regressions**

Run: `pytest tests/ -v`
Expected: All 113 tests pass (adding a module to fixtures shouldn't break anything).

- [ ] **Step 4: Commit**

```bash
git add tests/conftest.py
git commit -m "test: add second external unit fixture for multi-camera tests"
```

---

### Task 2: Refactor BticinoWebRTCCamera to accept module_id

**Files:**
- Modify: `custom_components/bticino_intercom/camera.py`
- Test: `tests/test_webrtc_camera.py`

- [ ] **Step 1: Write the failing test — two cameras created**

In `tests/test_webrtc_camera.py`, modify `test_webrtc_camera_created`:

```python
async def test_webrtc_cameras_created_per_external_unit(
    hass: HomeAssistant,
    mock_setup_entry: MockConfigEntry,
) -> None:
    """Test that one WebRTC camera is created per BNEU external unit."""
    states = hass.states.async_entity_ids(CAMERA_DOMAIN)
    webrtc_entities = [s for s in states if "live" in s.lower() or "citofono" in s.lower()]
    # Two BNEU modules in fixtures = two WebRTC cameras
    assert len(webrtc_entities) == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_webrtc_camera.py::test_webrtc_cameras_created_per_external_unit -v`
Expected: FAIL — currently only 1 WebRTC camera is created.

- [ ] **Step 3: Modify __init__ to accept module_id and module_name**

In `camera.py`, change `BticinoWebRTCCamera.__init__`:

```python
def __init__(
    self,
    coordinator: BticinoIntercomCoordinator,
    account: AsyncAccount,
    signaling_client: SignalingClient,
    module_id: str,
    module_name: str,
) -> None:
    """Initialize the WebRTC camera."""
    super().__init__(coordinator)
    Camera.__init__(self)
    self._account = account
    self._signaling = signaling_client
    self._module_id = module_id
    self._attr_unique_id = f"{coordinator.entry.entry_id}_webrtc_{module_id}"
    self._attr_name = module_name
    self._turn_servers: list[RTCIceServer] = []
    self._pending_candidates: list[RTCIceCandidateInit] = []
    self._session_ready = False
```

- [ ] **Step 4: Modify async_setup_entry to create one camera per BNEU**

In `camera.py`, replace the single `BticinoWebRTCCamera(coordinator, account, signaling_client)` in `async_setup_entry`:

```python
async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the BTicino camera platform."""
    coordinator: BticinoIntercomCoordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]

    if not coordinator.main_device_id:
        _LOGGER.warning("No bridge device ID found, cannot set up cameras.")
        return

    account: AsyncAccount = hass.data[DOMAIN][entry.entry_id]["account"]
    signaling_client: SignalingClient = hass.data[DOMAIN][entry.entry_id]["signaling_client"]

    entities: list[Camera] = [
        BticinoSnapshotCamera(coordinator),
        BticinoVignetteCamera(coordinator),
    ]

    # Create one WebRTC camera per external unit (BNEU module)
    for mid, mdata in coordinator.data.get("modules", {}).items():
        variant = mdata.get("variant", "")
        if "bneu_external_unit" in variant:
            entities.append(
                BticinoWebRTCCamera(
                    coordinator,
                    account,
                    signaling_client,
                    module_id=mid,
                    module_name=mdata.get("name", mid),
                )
            )

    async_add_entities(entities)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/test_webrtc_camera.py::test_webrtc_cameras_created_per_external_unit -v`
Expected: PASS

- [ ] **Step 6: Update test_webrtc_camera_snapshot_entities count**

The total camera count changes from 3 to 4 (2 snapshot/vignette + 2 WebRTC). Update `test_webrtc_camera_snapshot_entities`:

```python
async def test_webrtc_camera_snapshot_entities(
    hass: HomeAssistant,
    mock_setup_entry: MockConfigEntry,
) -> None:
    """Test that snapshot and vignette cameras are also created."""
    states = hass.states.async_entity_ids(CAMERA_DOMAIN)
    assert len(states) == 4  # snapshot + vignette + 2 webrtc
    names = {hass.states.get(s).attributes.get("friendly_name", "") for s in states}
    assert any("Snapshot" in n for n in names)
    assert any("Vignette" in n for n in names)
```

- [ ] **Step 7: Run full test suite**

Run: `pytest tests/ -v`
Expected: All tests pass (fix any that hardcoded camera count=3 or assumed single WebRTC entity).

- [ ] **Step 8: Commit**

```bash
git add custom_components/bticino_intercom/camera.py tests/test_webrtc_camera.py
git commit -m "feat: create one WebRTC camera per external unit (BNEU module)"
```

---

### Task 3: Use self._module_id in offer mode

**Files:**
- Modify: `custom_components/bticino_intercom/camera.py`
- Test: `tests/test_webrtc_camera.py`

- [ ] **Step 1: Write the failing test — offer uses specific module_id**

```python
async def test_offer_mode_sends_correct_module_id(
    hass: HomeAssistant,
    mock_setup_entry: MockConfigEntry,
) -> None:
    """In offer mode, send_offer should use the camera's own module_id."""
    from tests.conftest import EXTERNAL_UNIT_ID, EXTERNAL_UNIT_2_ID

    cameras = _get_all_webrtc_cameras(hass, mock_setup_entry)
    assert len(cameras) == 2

    for camera in cameras:
        camera.coordinator._active_call = None
        signaling = hass.data[DOMAIN][mock_setup_entry.entry_id]["signaling_client"]
        signaling.send_offer = AsyncMock(return_value="sess_new")
        signaling.send_answer = AsyncMock()
        signaling._is_connected = True
        signaling.is_connected = True

        messages = []
        await camera.async_handle_async_webrtc_offer(
            "v=0\r\na=setup:actpass\r\n", "test_sess", messages.append
        )

        # Verify send_offer was called with this camera's module_id
        call_kwargs = signaling.send_offer.call_args
        assert call_kwargs[1]["module_id"] == camera._module_id
```

Add helper to `test_webrtc_camera.py`:

```python
def _get_all_webrtc_cameras(hass, mock_setup_entry):
    """Helper to retrieve all WebRTC camera entity objects."""
    states = hass.states.async_entity_ids(CAMERA_DOMAIN)
    entity_comp = hass.data["entity_components"]["camera"]
    return [
        e for e in entity_comp.entities
        if hasattr(e, '_module_id')
    ]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_webrtc_camera.py::test_offer_mode_sends_correct_module_id -v`
Expected: FAIL — current offer mode scans for first BNEU instead of using `self._module_id`.

- [ ] **Step 3: Replace module_id scanning with self._module_id**

In `camera.py`, in `async_handle_async_webrtc_offer`, replace the offer mode block:

```python
else:
    # --- Offer mode: initiate on-demand call ---
    _LOGGER.info("Sending WebRTC offer to device %s (module=%s)", device_id, self._module_id)
    await self._signaling.send_offer(
        device_id=device_id,
        sdp=offer_sdp,
        module_id=self._module_id,
    )
```

This removes the for-loop that scanned modules and directly uses the camera's own `_module_id`.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_webrtc_camera.py::test_offer_mode_sends_correct_module_id -v`
Expected: PASS

- [ ] **Step 5: Run full test suite**

Run: `pytest tests/ -v`
Expected: All tests pass.

- [ ] **Step 6: Commit**

```bash
git add custom_components/bticino_intercom/camera.py tests/test_webrtc_camera.py
git commit -m "feat: use per-camera module_id in offer mode (no more first-match scanning)"
```

---

### Task 4: Match active_call module_id in answer mode

**Files:**
- Modify: `custom_components/bticino_intercom/camera.py`
- Test: `tests/test_webrtc_camera.py`

- [ ] **Step 1: Write the failing test — only matching camera answers**

```python
async def test_answer_mode_only_for_matching_module(
    hass: HomeAssistant,
    mock_setup_entry: MockConfigEntry,
) -> None:
    """Answer mode should only engage when active_call module_id matches this camera."""
    from tests.conftest import EXTERNAL_UNIT_ID, EXTERNAL_UNIT_2_ID

    cameras = _get_all_webrtc_cameras(hass, mock_setup_entry)
    # Find the camera for unit 1
    camera_1 = next(c for c in cameras if c._module_id == EXTERNAL_UNIT_ID)

    # Set up active call for unit 2 (not this camera)
    camera_1.coordinator._active_call = {
        "session_id": "sess_other",
        "tag_id": "tag",
        "device_id": "00:03:50:d9:a6:3b",
        "module_id": EXTERNAL_UNIT_2_ID,  # Different module!
        "sdp": "v=0\r\ndevice sdp\r\n",
    }

    signaling = hass.data[DOMAIN][mock_setup_entry.entry_id]["signaling_client"]
    signaling.send_offer = AsyncMock(return_value="sess_new")
    signaling.send_answer = AsyncMock()
    signaling._is_connected = True
    signaling.is_connected = True

    messages = []
    await camera_1.async_handle_async_webrtc_offer(
        "v=0\r\na=setup:actpass\r\n", "test_sess", messages.append
    )

    # Should use offer mode (not answer) because the call is for a different module
    signaling.send_offer.assert_called_once()
    signaling.send_answer.assert_not_called()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_webrtc_camera.py::test_answer_mode_only_for_matching_module -v`
Expected: FAIL — current code checks `active_call.get("sdp")` without matching module_id.

- [ ] **Step 3: Add module_id matching to answer mode condition**

In `camera.py`, change the answer mode condition from:

```python
active_call = self.coordinator.active_call
if active_call and active_call.get("sdp"):
```

to:

```python
active_call = self.coordinator.active_call
if (
    active_call
    and active_call.get("sdp")
    and active_call.get("module_id") == self._module_id
):
```

Also update the second occurrence (the flush condition at the end):

```python
if (
    active_call
    and active_call.get("sdp")
    and active_call.get("module_id") == self._module_id
):
    self._session_ready = True
    await self._flush_pending_candidates()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_webrtc_camera.py::test_answer_mode_only_for_matching_module -v`
Expected: PASS

- [ ] **Step 5: Update existing answer mode test**

The existing `test_answer_mode_when_active_call` needs to set a `module_id` that matches the camera. Update it to use the camera's own `_module_id`:

```python
async def test_answer_mode_when_active_call(
    hass: HomeAssistant,
    mock_setup_entry: MockConfigEntry,
) -> None:
    """When an active call with SDP exists for this module, send_answer is called."""
    cameras = _get_all_webrtc_cameras(hass, mock_setup_entry)
    camera = cameras[0]  # first BNEU camera

    camera.coordinator._active_call = {
        "session_id": "sess_incoming",
        "tag_id": "tag123",
        "device_id": "00:03:50:d9:a6:3b",
        "module_id": camera._module_id,  # Match this camera
        "sdp": "v=0\r\no=- 1 0 IN IP4 0.0.0.0\r\na=setup:actpass\r\n",
    }

    signaling = hass.data[DOMAIN][mock_setup_entry.entry_id]["signaling_client"]
    signaling.send_answer = AsyncMock()
    signaling.send_offer = AsyncMock(return_value="sess_new")
    signaling._is_connected = True
    signaling.is_connected = True

    offer_sdp = "v=0\r\no=- 9 0 IN IP4 0.0.0.0\r\na=setup:actpass\r\nm=audio 9 UDP/TLS/RTP/SAVPF 111\r\n"

    messages = []
    await camera.async_handle_async_webrtc_offer(offer_sdp, "test_session", messages.append)

    signaling.send_answer.assert_called_once()
    signaling.send_offer.assert_not_called()

    answer_sdp = signaling.send_answer.call_args[0][0]
    assert "a=setup:active" in answer_sdp

    from homeassistant.components.camera.webrtc import WebRTCAnswer
    answer_messages = [m for m in messages if isinstance(m, WebRTCAnswer)]
    assert len(answer_messages) == 1
    assert answer_messages[0].answer == "v=0\r\no=- 1 0 IN IP4 0.0.0.0\r\na=setup:actpass\r\n"
```

- [ ] **Step 6: Run full test suite**

Run: `pytest tests/ -v`
Expected: All tests pass.

- [ ] **Step 7: Commit**

```bash
git add custom_components/bticino_intercom/camera.py tests/test_webrtc_camera.py
git commit -m "feat: answer mode matches active_call module_id to camera's own module"
```
