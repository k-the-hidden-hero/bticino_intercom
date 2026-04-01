# BTicino Intercom v2.0 — WebRTC & Event Entity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add doorbell event entity (EventDeviceClass.DOORBELL) alongside existing binary_sensor, fix broken test fixtures for full test suite, and add tests for WebRTC camera entity.

**Architecture:** Event entity listens to the existing SIGNAL_CALL_RECEIVED dispatcher signal (same as binary_sensor) and fires discrete "ring" events. WebRTC camera is already implemented; we add tests for it. Broken test fixtures are fixed by adding `mock_setup_entry` to conftest.py. See `docs/architecture-webrtc-v2.md` for full architectural context.

**Tech Stack:** Python 3.14, Home Assistant custom component, pytest-homeassistant-custom-component, pybticino

---

## File Structure

| File | Action | Responsibility |
|------|--------|---------------|
| `custom_components/bticino_intercom/event.py` | **Create** | Doorbell event entity (EventDeviceClass.DOORBELL) |
| `custom_components/bticino_intercom/const.py` | **Modify** | Add Platform.EVENT to PLATFORMS |
| `custom_components/bticino_intercom/coordinator.py` | **Modify** | Dispatch event signal with event_type data |
| `tests/conftest.py` | **Modify** | Add `mock_setup_entry` fixture for full integration setup |
| `tests/test_event.py` | **Create** | Tests for doorbell event entity |
| `tests/test_webrtc_camera.py` | **Create** | Tests for WebRTC camera entity |
| `tests/test_token_persistence.py` | **Modify** | Fix broken HOME_NAME import |

---

### Task 1: Fix broken test infrastructure

**Files:**
- Modify: `tests/conftest.py`
- Modify: `tests/test_token_persistence.py`

- [ ] **Step 1: Add `mock_setup_entry` fixture to conftest.py**

This fixture is referenced by test_binary_sensor.py, test_sensor.py, and other test files but is missing. It sets up a full integration entry with mocked pybticino.

```python
# Add at the end of tests/conftest.py

@pytest.fixture
async def mock_setup_entry(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_modules_data: dict[str, Any],
) -> MockConfigEntry:
    """Set up the integration with mocked pybticino."""
    mock_config_entry.add_to_hass(hass)

    mock_account = AsyncMock()
    mock_account.homes = {HOME_ID: MagicMock(
        id=HOME_ID,
        name="Test Home",
        raw_data={"name": "Test Home", "id": HOME_ID},
        modules=[
            MagicMock(id=mid, raw_data=mdata)
            for mid, mdata in mock_modules_data.items()
        ],
    )}
    mock_account.async_update_topology = AsyncMock()
    mock_account.async_get_home_status = AsyncMock(return_value={
        "body": {"home": {"modules": list(mock_modules_data.values())}},
    })
    mock_account.async_get_events = AsyncMock(return_value={
        "body": {"home": {"events": []}},
    })
    mock_account.async_get_turn_servers = AsyncMock(return_value=[])

    mock_auth = AsyncMock()
    mock_auth.get_access_token = AsyncMock(return_value="fake_token")
    mock_auth.set_tokens = MagicMock()

    mock_ws = AsyncMock()
    mock_ws.connect = AsyncMock()
    mock_ws.disconnect = AsyncMock()
    mock_ws.get_listener_task = MagicMock(return_value=None)

    mock_signaling = AsyncMock()
    mock_signaling.is_connected = False

    with (
        patch("custom_components.bticino_intercom.AuthHandler", return_value=mock_auth),
        patch("custom_components.bticino_intercom.AsyncAccount", return_value=mock_account),
        patch("custom_components.bticino_intercom.WebsocketClient", return_value=mock_ws),
        patch("custom_components.bticino_intercom.SignalingClient", return_value=mock_signaling),
        patch("custom_components.bticino_intercom.Store") as mock_store_cls,
    ):
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_save = AsyncMock()
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

    return mock_config_entry
```

Also add necessary imports at the top of conftest.py:
```python
from unittest.mock import AsyncMock, MagicMock, patch
from pybticino import AsyncAccount
```

- [ ] **Step 2: Fix test_token_persistence.py import**

Already fixed: replace `from .conftest import HOME_ID, HOME_NAME` with:
```python
from .conftest import HOME_ID

HOME_NAME = "BTicino Test"
```

- [ ] **Step 3: Run all tests to verify fixture works**

Run: `python -m pytest tests/test_coordinator.py tests/test_camera.py tests/test_binary_sensor.py -v`
Expected: All pass (binary_sensor tests should now find the `mock_setup_entry` fixture)

- [ ] **Step 4: Commit**

```bash
git add tests/conftest.py tests/test_token_persistence.py
git commit -m "fix: add mock_setup_entry fixture, fix test imports"
```

---

### Task 2: Create doorbell event entity

**Files:**
- Create: `custom_components/bticino_intercom/event.py`
- Modify: `custom_components/bticino_intercom/const.py`

- [ ] **Step 1: Write the event entity test**

Create `tests/test_event.py`:

```python
"""Tests for the BTicino doorbell event entity."""

from homeassistant.components.event import DOMAIN as EVENT_DOMAIN, EventDeviceClass
from homeassistant.core import HomeAssistant
from homeassistant.helpers.dispatcher import async_dispatcher_send
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.bticino_intercom.const import DOMAIN, SIGNAL_CALL_RECEIVED

from .conftest import EXTERNAL_UNIT_ID


async def test_event_entity_created(
    hass: HomeAssistant,
    mock_setup_entry: MockConfigEntry,
) -> None:
    """Test that a doorbell event entity is created for external unit."""
    states = hass.states.async_entity_ids(EVENT_DOMAIN)
    assert len(states) >= 1
    # Find the doorbell event entity
    doorbell_entities = [s for s in states if "doorbell" in s or "call" in s]
    assert len(doorbell_entities) >= 1


async def test_event_fires_on_call(
    hass: HomeAssistant,
    mock_setup_entry: MockConfigEntry,
) -> None:
    """Test that event entity fires when a call is received."""
    # Get the event entity
    states = hass.states.async_entity_ids(EVENT_DOMAIN)
    assert len(states) >= 1

    entity_id = states[0]

    # Simulate incoming call via dispatcher
    async_dispatcher_send(hass, SIGNAL_CALL_RECEIVED, True, EXTERNAL_UNIT_ID)
    await hass.async_block_till_done()

    state = hass.states.get(entity_id)
    assert state is not None
    assert state.attributes.get("event_type") == "ring"


async def test_event_has_doorbell_device_class(
    hass: HomeAssistant,
    mock_setup_entry: MockConfigEntry,
) -> None:
    """Test that the event entity has DOORBELL device class."""
    states = hass.states.async_entity_ids(EVENT_DOMAIN)
    assert len(states) >= 1
    entity_id = states[0]
    state = hass.states.get(entity_id)
    assert state.attributes.get("device_class") == EventDeviceClass.DOORBELL
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_event.py -v`
Expected: FAIL — no event platform registered

- [ ] **Step 3: Add Platform.EVENT to PLATFORMS in const.py**

In `custom_components/bticino_intercom/const.py`, modify:

```python
PLATFORMS: list[Platform] = [
    Platform.LOCK,
    Platform.BINARY_SENSOR,
    Platform.SENSOR,
    Platform.LIGHT,
    Platform.CAMERA,
    Platform.EVENT,
]
```

- [ ] **Step 4: Create event.py**

Create `custom_components/bticino_intercom/event.py`:

```python
"""Platform for doorbell event integration."""

import logging
from typing import Any

from homeassistant.components.event import EventDeviceClass, EventEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, SIGNAL_CALL_RECEIVED, SUBTYPE_EXTERNAL_UNIT
from .coordinator import BticinoIntercomCoordinator
from .entity import BticinoEntity

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the BTicino event platform."""
    coordinator: BticinoIntercomCoordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]

    entities = []
    if coordinator.data and "modules" in coordinator.data:
        for module_id, module_data in coordinator.data["modules"].items():
            variant = module_data.get("variant", "")
            subtype = variant.split(":", 1)[1] if ":" in variant else None
            if subtype == SUBTYPE_EXTERNAL_UNIT:
                entities.append(BticinoDoorbellEvent(coordinator, module_id))

    async_add_entities(entities)


class BticinoDoorbellEvent(BticinoEntity, EventEntity):
    """Event entity for BTicino doorbell ring."""

    _attr_device_class = EventDeviceClass.DOORBELL
    _attr_event_types = ["ring"]
    _attr_icon = "mdi:doorbell-video"

    def __init__(self, coordinator: BticinoIntercomCoordinator, module_id: str) -> None:
        """Initialize the doorbell event entity."""
        super().__init__(coordinator, module_id)
        self._attr_unique_id = f"{coordinator.entry.entry_id}_doorbell_event_{module_id}"
        module_data = coordinator.data.get("modules", {}).get(module_id, {})
        self._attr_name = module_data.get("name") or "Doorbell"

    @callback
    def _handle_call_received(self, state: bool, module_id: str | None) -> None:
        """Handle the dispatcher signal for incoming calls."""
        if module_id == self._module_id and state:
            _LOGGER.debug("Doorbell event triggered for %s", self.entity_id)
            self._trigger_event("ring", {"module_id": module_id})
            self.async_write_ha_state()

    async def async_added_to_hass(self) -> None:
        """Register for call events."""
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                SIGNAL_CALL_RECEIVED,
                self._handle_call_received,
            )
        )
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/test_event.py -v`
Expected: 3 tests PASS

- [ ] **Step 6: Commit**

```bash
git add custom_components/bticino_intercom/event.py custom_components/bticino_intercom/const.py tests/test_event.py
git commit -m "feat: add doorbell event entity (EventDeviceClass.DOORBELL)

Add event.py with BticinoDoorbellEvent that fires 'ring' events when
a call is received via the SIGNAL_CALL_RECEIVED dispatcher. Runs
alongside the existing binary_sensor for backwards compatibility."
```

---

### Task 3: Add WebRTC camera tests

**Files:**
- Create: `tests/test_webrtc_camera.py`

- [ ] **Step 1: Write WebRTC camera tests**

Create `tests/test_webrtc_camera.py`:

```python
"""Tests for the BTicino WebRTC camera entity."""

from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.components.camera import DOMAIN as CAMERA_DOMAIN, CameraEntityFeature
from homeassistant.components.camera.webrtc import WebRTCAnswer, WebRTCError
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from .conftest import BRIDGE_MAC, EXTERNAL_UNIT_ID


async def test_webrtc_camera_created(
    hass: HomeAssistant,
    mock_setup_entry: MockConfigEntry,
) -> None:
    """Test that a WebRTC camera entity is created."""
    states = hass.states.async_entity_ids(CAMERA_DOMAIN)
    webrtc_entities = [s for s in states if "live_video" in s]
    assert len(webrtc_entities) == 1


async def test_webrtc_camera_has_stream_feature(
    hass: HomeAssistant,
    mock_setup_entry: MockConfigEntry,
) -> None:
    """Test that the WebRTC camera supports STREAM feature."""
    states = hass.states.async_entity_ids(CAMERA_DOMAIN)
    webrtc_entities = [s for s in states if "live_video" in s]
    assert len(webrtc_entities) == 1

    state = hass.states.get(webrtc_entities[0])
    supported = state.attributes.get("supported_features", 0)
    assert supported & CameraEntityFeature.STREAM


async def test_webrtc_camera_snapshot_entities(
    hass: HomeAssistant,
    mock_setup_entry: MockConfigEntry,
) -> None:
    """Test that snapshot and vignette cameras are also created."""
    states = hass.states.async_entity_ids(CAMERA_DOMAIN)
    assert len(states) == 3  # snapshot + vignette + webrtc
    names = {hass.states.get(s).attributes.get("friendly_name", "") for s in states}
    assert any("Snapshot" in n for n in names)
    assert any("Vignette" in n for n in names)
    assert any("Live Video" in n for n in names)
```

- [ ] **Step 2: Run tests to verify they pass**

Run: `python -m pytest tests/test_webrtc_camera.py -v`
Expected: 3 tests PASS

- [ ] **Step 3: Commit**

```bash
git add tests/test_webrtc_camera.py
git commit -m "test: add WebRTC camera entity tests

Verify camera entity creation, STREAM feature support,
and co-existence with snapshot/vignette cameras."
```

---

### Task 4: Run full test suite and fix remaining issues

**Files:**
- Possibly modify: various test files to fix fixture issues

- [ ] **Step 1: Run full test suite**

Run: `python -m pytest tests/ -v 2>&1 | tail -30`
Expected: Identify any remaining failures

- [ ] **Step 2: Fix any remaining failures**

Address failures one by one. Common issues:
- Missing fixtures (add to conftest.py)
- Import errors (fix paths)
- Mock mismatches (update mock data)

- [ ] **Step 3: Run full suite green**

Run: `python -m pytest tests/ -v`
Expected: All tests PASS

- [ ] **Step 4: Commit**

```bash
git add -u
git commit -m "fix: resolve remaining test failures for full green suite"
```

---

### Task 5: Final verification and commit

- [ ] **Step 1: Verify syntax on all modified/new files**

```bash
python -c "
import ast
for f in ['event', 'const', 'coordinator', 'camera', '__init__', 'binary_sensor']:
    ast.parse(open(f'custom_components/bticino_intercom/{f}.py').read())
    print(f'{f}.py: OK')
"
```

- [ ] **Step 2: Run ruff lint**

```bash
python -m ruff check custom_components/bticino_intercom/ tests/
```

- [ ] **Step 3: Run full test suite one final time**

```bash
python -m pytest tests/ -v --tb=short
```

- [ ] **Step 4: Push to remote**

```bash
git push origin dev/webrtc
```
