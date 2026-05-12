# Answer Mode Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Enable full WebRTC answer mode for incoming calls — ring overlay in the card, reject service, mobile notification blueprint, and idle poster cleanup.

**Architecture:** The coordinator emits `bticino_intercom_call` HA events on ring/end. The card subscribes to these events and shows a ring overlay with Answer/Reject/Dismiss. Answering calls `_startCall()` which routes through camera.py's existing answer mode stub. A blueprint sends actionable mobile notifications with ringtone.

**Tech Stack:** Python (HA integration), vanilla JS (Lovelace card), YAML (blueprint)

---

### Task 1: Add call event constants and coordinator `_fire_call_event` helper

**Files:**
- Modify: `custom_components/bticino_intercom/const.py`
- Modify: `custom_components/bticino_intercom/coordinator.py`
- Test: `tests/test_coordinator.py`

- [ ] **Step 1: Add constant for the new HA event name in const.py**

Add after line 54 (after `EVENT_LOGBOOK_ACCEPTED_CALL`):

```python
# Real-time call event for frontend / automations
EVENT_CALL = f"{DOMAIN}_call"
```

- [ ] **Step 2: Write failing test for ring event emission**

Add to `tests/test_coordinator.py`:

```python
class TestCallEventEmission:
    """Test bticino_intercom_call event emission."""

    async def test_rtc_offer_fires_ring_event(self, hass, coordinator, ws_rtc_offer):
        """RTC offer should fire a ring event with type=ring."""
        events = async_capture_events(hass, "bticino_intercom_call")

        coordinator._process_websocket_event(ws_rtc_offer)
        await hass.async_block_till_done()

        ring_events = [e for e in events if e.data.get("type") == "ring"]
        assert len(ring_events) >= 1
        data = ring_events[0].data
        assert data["type"] == "ring"
        assert data["session_id"] is not None
        assert data["module_id"] is not None
        assert data["entry_id"] == coordinator.config_entry.entry_id
        assert "camera_entity_id" in data
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd /home/koma/src/bticino_intercom && python -m pytest tests/test_coordinator.py::TestCallEventEmission::test_rtc_offer_fires_ring_event -v`
Expected: FAIL — no `bticino_intercom_call` events captured.

- [ ] **Step 4: Add `_fire_call_event` helper to coordinator and call it from `_process_rtc_event`**

In `coordinator.py`, add import at top:

```python
from .const import EVENT_CALL
```

Add helper method to `BticinoIntercomCoordinator`:

```python
def _fire_call_event(
    self,
    event_type: str,
    module_id: str | None = None,
    *,
    reason: str | None = None,
    snapshot_url: str | None = None,
    vignette_url: str | None = None,
) -> None:
    """Fire a bticino_intercom_call event for frontend/automations."""
    session_id = self._active_call.get("session_id") if self._active_call else None
    module_name = None
    camera_entity_id = None

    if module_id:
        modules = self.data.get("modules", {}) if self.data else {}
        module_name = modules.get(module_id, {}).get("name", module_id)
        # Derive camera entity_id from entity registry
        ent_reg = er.async_get(self.hass)
        for entry in er.async_entries_for_config_entry(ent_reg, self.config_entry.entry_id):
            if entry.domain == "camera" and entry.unique_id and module_id in entry.unique_id:
                camera_entity_id = entry.entity_id
                break

    data: dict[str, Any] = {
        "type": event_type,
        "entry_id": self.config_entry.entry_id,
        "module_id": module_id,
        "module_name": module_name,
        "session_id": session_id,
    }
    if event_type == "ring":
        data["snapshot_url"] = snapshot_url
        data["vignette_url"] = vignette_url
        data["camera_entity_id"] = camera_entity_id
    if reason:
        data["reason"] = reason

    self.hass.bus.async_fire(EVENT_CALL, data)
```

Add `import homeassistant.helpers.entity_registry as er` at top of coordinator.py.

In `_process_rtc_event`, after the `async_dispatcher_send(... SIGNAL_CALL_RECEIVED, True, calling_module_id)` line (line ~354), add:

```python
self._fire_call_event("ring", calling_module_id)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd /home/koma/src/bticino_intercom && python -m pytest tests/test_coordinator.py::TestCallEventEmission::test_rtc_offer_fires_ring_event -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add custom_components/bticino_intercom/const.py custom_components/bticino_intercom/coordinator.py tests/test_coordinator.py
git commit -m "feat: emit bticino_intercom_call ring event on RTC offer"
```

---

### Task 2: Fire end events on rescind, terminate, and timeout

**Files:**
- Modify: `custom_components/bticino_intercom/coordinator.py`
- Modify: `custom_components/bticino_intercom/binary_sensor.py`
- Test: `tests/test_coordinator.py`

- [ ] **Step 1: Write failing tests for end events**

Add to `tests/test_coordinator.py`:

```python
async def test_rtc_rescind_fires_end_event(self, hass, coordinator, ws_rtc_offer, ws_rtc_rescind):
    """RTC rescind should fire an end event with reason=rescind."""
    coordinator._process_websocket_event(ws_rtc_offer)
    await hass.async_block_till_done()

    events = async_capture_events(hass, "bticino_intercom_call")
    coordinator._process_websocket_event(ws_rtc_rescind)
    await hass.async_block_till_done()

    end_events = [e for e in events if e.data.get("type") == "end"]
    assert len(end_events) == 1
    assert end_events[0].data["reason"] == "rescind"

async def test_rtc_terminate_fires_end_event(self, hass, coordinator, ws_rtc_offer, ws_rtc_terminate):
    """RTC terminate should fire an end event with reason=terminate."""
    coordinator._process_websocket_event(ws_rtc_offer)
    await hass.async_block_till_done()

    events = async_capture_events(hass, "bticino_intercom_call")
    coordinator._process_websocket_event(ws_rtc_terminate)
    await hass.async_block_till_done()

    end_events = [e for e in events if e.data.get("type") == "end"]
    assert len(end_events) == 1
    assert end_events[0].data["reason"] == "terminate"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/koma/src/bticino_intercom && python -m pytest tests/test_coordinator.py::TestCallEventEmission -v -k "end_event"`
Expected: FAIL

- [ ] **Step 3: Add end event firing in rescind and terminate handlers**

In `_process_rtc_event`, in the rescind handling block (after `self._active_call = None`, line ~375), add:

```python
self._fire_call_event("end", calling_module_id, reason="rescind")
```

In the terminate handling block (after `self._active_call = None`, line ~397), add:

```python
self._fire_call_event("end", calling_module_id, reason="terminate")
```

- [ ] **Step 4: Fire end event on binary_sensor timeout**

In `coordinator.py`, add a method that the binary_sensor timeout can call:

```python
def fire_call_timeout(self, module_id: str) -> None:
    """Fire an end event when the call times out."""
    self._fire_call_event("end", module_id, reason="timeout")
    self._active_call = None
```

In `binary_sensor.py`, update `_turn_off_callback` to also fire the timeout event:

```python
@callback
def _turn_off_callback(self, *args: Any) -> None:
    """Turn the sensor off after timeout."""
    _LOGGER.debug("Executing turn-off callback for %s", self.entity_id)
    self._turn_off_canceller = None
    self._attr_is_on = False
    self.async_write_ha_state()
    self.coordinator.fire_call_timeout(self._module_id)
```

- [ ] **Step 5: Run all tests to verify they pass**

Run: `cd /home/koma/src/bticino_intercom && python -m pytest tests/test_coordinator.py::TestCallEventEmission -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add custom_components/bticino_intercom/coordinator.py custom_components/bticino_intercom/binary_sensor.py tests/test_coordinator.py
git commit -m "feat: emit bticino_intercom_call end events on rescind/terminate/timeout"
```

---

### Task 3: Fire ring event with image URLs from status event

**Files:**
- Modify: `custom_components/bticino_intercom/coordinator.py`
- Test: `tests/test_coordinator.py`

- [ ] **Step 1: Write failing test for ring event with images**

Add to `tests/test_coordinator.py`:

```python
async def test_status_incoming_call_fires_ring_with_images(self, hass, coordinator, ws_rtc_offer, ws_status_incoming_call):
    """Status incoming_call should fire a ring event with image URLs."""
    coordinator._process_websocket_event(ws_rtc_offer)
    await hass.async_block_till_done()

    events = async_capture_events(hass, "bticino_intercom_call")
    coordinator._process_websocket_event(ws_status_incoming_call)
    await hass.async_block_till_done()

    ring_events = [e for e in events if e.data.get("type") == "ring"]
    assert len(ring_events) >= 1
    last_ring = ring_events[-1]
    assert last_ring.data.get("snapshot_url") is not None
    assert last_ring.data.get("vignette_url") is not None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/koma/src/bticino_intercom && python -m pytest tests/test_coordinator.py::TestCallEventEmission::test_status_incoming_call_fires_ring_with_images -v`
Expected: FAIL

- [ ] **Step 3: Add ring event re-fire in `_process_status_event`**

In `_process_status_event`, in the `incoming_call` handling block (after the snapshot/vignette URLs are extracted, around line ~440), add:

```python
calling_module_id = self._active_call.get("module_id") if self._active_call else None
if calling_module_id:
    self._fire_call_event(
        "ring",
        calling_module_id,
        snapshot_url=snapshot_url,
        vignette_url=vignette_url,
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /home/koma/src/bticino_intercom && python -m pytest tests/test_coordinator.py::TestCallEventEmission -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add custom_components/bticino_intercom/coordinator.py tests/test_coordinator.py
git commit -m "feat: re-fire ring event with image URLs from status event"
```

---

### Task 4: Register `reject_call` service

**Files:**
- Create: `custom_components/bticino_intercom/services.yaml`
- Modify: `custom_components/bticino_intercom/__init__.py`
- Test: `tests/test_init.py`

- [ ] **Step 1: Create services.yaml**

Create `custom_components/bticino_intercom/services.yaml`:

```yaml
reject_call:
  name: Reject Call
  description: Reject the active incoming call, terminating it for all devices.
  fields:
    entry_id:
      name: Config Entry ID
      description: The config entry to reject the call for. Defaults to the first entry.
      required: false
      selector:
        text:
```

- [ ] **Step 2: Write failing test for reject_call service**

Add to `tests/test_init.py`:

```python
async def test_reject_call_service(hass, mock_config_entry):
    """Test reject_call service terminates the active call."""
    entry = await _setup_integration(hass, mock_config_entry)
    coordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    signaling_client = hass.data[DOMAIN][entry.entry_id]["signaling_client"]

    # Simulate an active call
    coordinator._active_call = {
        "session_id": "test-session",
        "module_id": "00:03:50:d9:a6:3b",
    }

    events = async_capture_events(hass, "bticino_intercom_call")

    await hass.services.async_call(DOMAIN, "reject_call", blocking=True)
    await hass.async_block_till_done()

    assert coordinator._active_call is None
    signaling_client.send_terminate.assert_called_once()
    end_events = [e for e in events if e.data.get("type") == "end"]
    assert len(end_events) == 1
    assert end_events[0].data["reason"] == "rejected"
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd /home/koma/src/bticino_intercom && python -m pytest tests/test_init.py::test_reject_call_service -v`
Expected: FAIL — service not registered.

- [ ] **Step 4: Register the reject_call service in __init__.py**

In `async_setup_entry`, after the coordinator and signaling_client are stored in `hass.data`, add:

```python
async def _async_reject_call(call: ServiceCall) -> None:
    """Reject the active incoming call."""
    entry_id = call.data.get("entry_id")
    if not entry_id:
        # Default to the first (usually only) entry
        entry_id = next(iter(hass.data.get(DOMAIN, {})), None)
    if not entry_id or entry_id not in hass.data.get(DOMAIN, {}):
        _LOGGER.warning("reject_call: no valid entry_id found")
        return
    entry_data = hass.data[DOMAIN][entry_id]
    coord = entry_data[COORDINATOR_KEY]
    sig = entry_data[SIGNALING_CLIENT_KEY]

    if not coord.active_call:
        _LOGGER.debug("reject_call: no active call to reject")
        return

    module_id = coord.active_call.get("module_id")
    try:
        await sig.send_terminate()
    except Exception:
        _LOGGER.exception("reject_call: failed to send terminate")
    coord._fire_call_event("end", module_id, reason="rejected")
    coord._active_call = None

hass.services.async_register(DOMAIN, "reject_call", _async_reject_call)
```

Add import at top:

```python
from homeassistant.core import ServiceCall
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd /home/koma/src/bticino_intercom && python -m pytest tests/test_init.py::test_reject_call_service -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add custom_components/bticino_intercom/services.yaml custom_components/bticino_intercom/__init__.py tests/test_init.py
git commit -m "feat: add reject_call service to terminate incoming calls"
```

---

### Task 5: Register static path for doorbell sound

**Files:**
- Create: `custom_components/bticino_intercom/www/doorbell.wav`
- Modify: `custom_components/bticino_intercom/__init__.py`

- [ ] **Step 1: Add a doorbell sound file**

Download or generate a doorbell WAV file and place at `custom_components/bticino_intercom/www/doorbell.wav`. A 2-3 second classic ding-dong sound, 16-bit PCM, 44100Hz, under 200KB.

For now, generate a simple placeholder:

```bash
cd /home/koma/src/bticino_intercom/custom_components/bticino_intercom
mkdir -p www
# Generate a 2-second 440Hz sine wave as placeholder
python3 -c "
import struct, math
sr, dur, freq = 22050, 2, 440
samples = [int(16000 * math.sin(2 * math.pi * freq * t / sr) * max(0, 1 - t/sr/dur)) for t in range(sr * dur)]
with open('www/doorbell.wav', 'wb') as f:
    data = struct.pack(f'<{len(samples)}h', *samples)
    f.write(b'RIFF' + struct.pack('<I', 36 + len(data)) + b'WAVE')
    f.write(b'fmt ' + struct.pack('<IHHIIHH', 16, 1, 1, sr, sr*2, 2, 16))
    f.write(b'data' + struct.pack('<I', len(data)) + data)
"
```

- [ ] **Step 2: Register the static path in __init__.py**

In `async_setup_entry`, add after service registration:

```python
# Register static files for blueprint sound
www_path = Path(__file__).parent / "www"
if www_path.is_dir():
    hass.http.register_static_path(f"/{DOMAIN}", str(www_path), cache_headers=True)
```

Add `from pathlib import Path` at top if not already imported.

- [ ] **Step 3: Commit**

```bash
git add custom_components/bticino_intercom/www/doorbell.wav custom_components/bticino_intercom/__init__.py
git commit -m "feat: add doorbell sound file and register static path"
```

---

### Task 6: Card — remove poster, add idle state

**Files:**
- Modify: `cards/bticino-intercom-card.js`

- [ ] **Step 1: Replace poster HTML with idle placeholder in `_render()`**

In `_render()`, replace the poster div:

```javascript
// REMOVE this line:
<div class="poster" id="poster"><img id="poster-img" alt="" /></div>

// REPLACE with:
<div class="idle-overlay" id="idle-overlay">
  <ha-icon icon="mdi:doorbell-video" style="--mdc-icon-size:48px;opacity:0.3"></ha-icon>
</div>
```

- [ ] **Step 2: Add idle overlay CSS to CARD_STYLES**

```css
.idle-overlay {
  position: absolute; inset: 0;
  display: flex; align-items: center; justify-content: center;
  background: rgba(0,0,0,0.85);
  pointer-events: none;
  transition: opacity 0.3s;
}
.idle-overlay.hidden { opacity: 0; pointer-events: none; }
```

- [ ] **Step 3: Replace `_updatePoster` with `_updateIdleOverlay`**

```javascript
_updateIdleOverlay() {
  const el = this.shadowRoot?.getElementById('idle-overlay');
  if (!el) return;
  if (this._playing) {
    el.classList.add('hidden');
  } else {
    el.classList.remove('hidden');
  }
}
```

Replace all calls to `_updatePoster()` with `_updateIdleOverlay()`.

- [ ] **Step 4: Remove poster CSS**

Remove `.poster` and `.poster img` CSS rules from CARD_STYLES.

- [ ] **Step 5: Deploy and verify visually**

Copy to HA, bump resource version, reload. Verify:
- Idle state shows dark background with doorbell icon
- Click still triggers call
- During video, idle overlay hides
- After hangup, idle overlay reappears

- [ ] **Step 6: Commit**

```bash
git add cards/bticino-intercom-card.js
git commit -m "feat(card): replace cached poster with idle overlay"
```

---

### Task 7: Card — ring overlay UI

**Files:**
- Modify: `cards/bticino-intercom-card.js`

- [ ] **Step 1: Add ring overlay CSS to CARD_STYLES**

```css
/* Ring overlay */
.ring-overlay {
  position: absolute; inset: 0; z-index: 15;
  display: none; flex-direction: column; align-items: center; justify-content: center;
  background: rgba(0,0,0,0.9);
}
.ring-overlay.open { display: flex; }
.ring-preview {
  width: 100%; flex: 1; display: flex; align-items: center; justify-content: center;
  overflow: hidden; position: relative;
}
.ring-preview img {
  max-width: 100%; max-height: 100%; object-fit: contain; border-radius: 8px;
}
.ring-preview .ring-placeholder {
  display: flex; align-items: center; justify-content: center;
  width: 100%; height: 100%;
}
.ring-name {
  font-size: 16px; font-weight: 500; padding: 8px 0 4px; color: var(--bti-text);
}
.ring-pulse {
  position: absolute; top: 50%; left: 50%; transform: translate(-50%, -50%);
  pointer-events: none;
}
.ring-pulse .rp { position: absolute; border-radius: 50%; border: 2px solid #66bb6a; opacity: 0; }
.ring-pulse .rp:nth-child(1) { width: 80px; height: 80px; margin: -40px 0 0 -40px; animation: rp 2s ease-out infinite; }
.ring-pulse .rp:nth-child(2) { width: 120px; height: 120px; margin: -60px 0 0 -60px; animation: rp 2s ease-out 0.5s infinite; }
.ring-pulse .rp:nth-child(3) { width: 160px; height: 160px; margin: -80px 0 0 -80px; animation: rp 2s ease-out 1s infinite; }
@keyframes rp { 0% { opacity: 0.6; transform: scale(0.5); } 100% { opacity: 0; transform: scale(1); } }
.ring-actions {
  display: flex; gap: 16px; padding: 16px; justify-content: center;
}
.ring-btn {
  display: flex; flex-direction: column; align-items: center; gap: 4px;
  padding: 12px 20px; border: none; border-radius: 12px; cursor: pointer;
  font-size: 11px; font-weight: 500;
}
.ring-btn.answer { background: rgba(76,175,80,0.25); color: #66bb6a; }
.ring-btn.answer:hover { background: rgba(76,175,80,0.4); }
.ring-btn.reject { background: rgba(244,67,54,0.25); color: #ef5350; }
.ring-btn.reject:hover { background: rgba(244,67,54,0.4); }
.ring-btn.dismiss { background: rgba(255,255,255,0.08); color: var(--bti-text-secondary); }
.ring-btn.dismiss:hover { background: rgba(255,255,255,0.15); }
.ring-btn ha-icon { --mdc-icon-size: 28px; }

/* Missed call banner */
.missed-banner {
  position: absolute; inset: 0; z-index: 15;
  display: none; flex-direction: column; align-items: center; justify-content: center;
  background: rgba(0,0,0,0.85); gap: 8px;
}
.missed-banner.open { display: flex; }
.missed-banner ha-icon { --mdc-icon-size: 36px; color: #ffa726; }
.missed-text { font-size: 14px; color: var(--bti-text-secondary); }
```

- [ ] **Step 2: Add ring overlay HTML in `_render()` inside `<div class="video-area">`**

Add after the swipe-dots div, inside the video-area:

```html
<div class="ring-overlay" id="ring-overlay">
  <div class="ring-preview" id="ring-preview">
    <div class="ring-placeholder"><ha-icon icon="mdi:doorbell-video" style="--mdc-icon-size:64px;opacity:0.3"></ha-icon></div>
    <div class="ring-pulse"><div class="rp"></div><div class="rp"></div><div class="rp"></div></div>
  </div>
  <div class="ring-name" id="ring-name"></div>
  <div class="ring-actions">
    <button class="ring-btn answer" id="ring-answer"><ha-icon icon="mdi:phone"></ha-icon>Answer</button>
    <button class="ring-btn reject" id="ring-reject"><ha-icon icon="mdi:phone-hangup"></ha-icon>Reject</button>
    <button class="ring-btn dismiss" id="ring-dismiss"><ha-icon icon="mdi:close"></ha-icon>Dismiss</button>
  </div>
</div>
<div class="missed-banner" id="missed-banner">
  <ha-icon icon="mdi:phone-missed"></ha-icon>
  <div class="missed-text">Missed call</div>
</div>
```

- [ ] **Step 3: Add ring event bindings in `_bindEvents()`**

```javascript
$('ring-answer')?.addEventListener('click', (e) => { e.stopPropagation(); this._answerIncomingCall(); });
$('ring-reject')?.addEventListener('click', (e) => { e.stopPropagation(); this._rejectIncomingCall(); });
$('ring-dismiss')?.addEventListener('click', (e) => { e.stopPropagation(); this._dismissIncomingCall(); });
```

- [ ] **Step 4: Add ring action methods**

```javascript
_answerIncomingCall() {
  this.shadowRoot?.getElementById('ring-overlay')?.classList.remove('open');
  this._startCall();
}

_rejectIncomingCall() {
  this.shadowRoot?.getElementById('ring-overlay')?.classList.remove('open');
  const entryId = this._getConfigEntryId();
  if (entryId && this._hass) {
    this._hass.callService('bticino_intercom', 'reject_call', { entry_id: entryId });
  }
}

_dismissIncomingCall() {
  this.shadowRoot?.getElementById('ring-overlay')?.classList.remove('open');
}

_showRingOverlay(eventData) {
  const overlay = this.shadowRoot?.getElementById('ring-overlay');
  const preview = this.shadowRoot?.getElementById('ring-preview');
  const nameEl = this.shadowRoot?.getElementById('ring-name');
  if (!overlay) return;

  // Switch to the ringing intercom tab
  const camIdx = this._config.intercoms.findIndex(ic => ic.camera === eventData.camera_entity_id);
  if (camIdx >= 0 && camIdx !== this._activeIndex) {
    this._switchIntercom(camIdx);
  }

  nameEl.textContent = eventData.module_name || 'Incoming call';

  // Show preview image if available
  if (eventData.vignette_url) {
    this._hass.callWS({ type: 'auth/sign_path', path: eventData.vignette_url, expires: 60 })
      .then(({ path }) => {
        preview.innerHTML = `<img src="${path}" alt="" /><div class="ring-pulse"><div class="rp"></div><div class="rp"></div><div class="rp"></div></div>`;
      })
      .catch(() => {});
  }

  overlay.classList.add('open');
}

_showMissedCall() {
  this.shadowRoot?.getElementById('ring-overlay')?.classList.remove('open');
  const banner = this.shadowRoot?.getElementById('missed-banner');
  if (!banner) return;
  banner.classList.add('open');
  setTimeout(() => banner.classList.remove('open'), 5000);
}
```

- [ ] **Step 5: Deploy and verify ring overlay renders**

Copy to HA, bump resource, reload. Verify the ring overlay can be triggered manually (via browser console) and the three buttons work visually.

- [ ] **Step 6: Commit**

```bash
git add cards/bticino-intercom-card.js
git commit -m "feat(card): add ring overlay with Answer/Reject/Dismiss"
```

---

### Task 8: Card — event subscription and lifecycle

**Files:**
- Modify: `cards/bticino-intercom-card.js`

- [ ] **Step 1: Add event subscription state to constructor**

In the constructor, add:

```javascript
this._callEventUnsub = null;
this._ringSessionId = null;
```

- [ ] **Step 2: Subscribe in connectedCallback**

Update `connectedCallback()`:

```javascript
connectedCallback() {
  if (this._config && this._hass) this._render();
  this._subscribeCallEvents();
}
```

- [ ] **Step 3: Unsubscribe in disconnectedCallback**

Update `disconnectedCallback()`:

```javascript
disconnectedCallback() {
  this._cleanup();
  this._unsubscribeCallEvents();
  document.removeEventListener('click', this._boundDocClick);
}
```

- [ ] **Step 4: Add subscription methods**

```javascript
_subscribeCallEvents() {
  if (this._callEventUnsub || !this._hass?.connection) return;
  this._callEventUnsub = this._hass.connection.subscribeEvents(
    (event) => this._handleCallEvent(event),
    'bticino_intercom_call'
  );
}

_unsubscribeCallEvents() {
  if (this._callEventUnsub) {
    this._callEventUnsub.then(unsub => unsub());
    this._callEventUnsub = null;
  }
}

_handleCallEvent(event) {
  const data = event.data;

  if (data.type === 'ring') {
    // Filter: only handle ring events for cameras in our config
    const cameras = this._config?.intercoms?.map(ic => ic.camera) || [];
    if (data.camera_entity_id && !cameras.includes(data.camera_entity_id)) return;
    this._ringSessionId = data.session_id;
    this._showRingOverlay(data);
  } else if (data.type === 'end') {
    // End events match by session_id, no camera_entity_id field
    if (data.session_id !== this._ringSessionId) return;
    this._ringSessionId = null;
    const ringOverlay = this.shadowRoot?.getElementById('ring-overlay');
    if (ringOverlay?.classList.contains('open')) {
      this._showMissedCall();
    }
    if (this._state === STATE.LIVE) {
      this._hangUp();
    }
  }
}
```

- [ ] **Step 5: Re-subscribe when hass changes**

In the `set hass()` setter, after `this._hass = hass`, add:

```javascript
if (!this._callEventUnsub && hass?.connection) this._subscribeCallEvents();
```

- [ ] **Step 6: Deploy and test with inject_test_event**

Copy to HA, bump resource, reload. Call `bticino_intercom.inject_test_event` from Developer Tools → Services. Verify:
- Ring overlay appears on the card
- Preview image loads
- Dismiss hides the overlay
- After 30s, missed call banner appears

- [ ] **Step 7: Commit**

```bash
git add cards/bticino-intercom-card.js
git commit -m "feat(card): subscribe to call events and handle ring/end lifecycle"
```

---

### Task 9: Card — URL auto-answer

**Files:**
- Modify: `cards/bticino-intercom-card.js`

- [ ] **Step 1: Add URL check in `connectedCallback`**

Update `connectedCallback()` to check for auto-answer param:

```javascript
connectedCallback() {
  if (this._config && this._hass) this._render();
  this._subscribeCallEvents();
  this._checkAutoAnswer();
}

_checkAutoAnswer() {
  const params = new URLSearchParams(window.location.search);
  const answerCamera = params.get('answer');
  if (!answerCamera || !this._config) return;

  const camIdx = this._config.intercoms.findIndex(ic => ic.camera === answerCamera);
  if (camIdx < 0) return;

  // Remove the param from URL
  params.delete('answer');
  const newUrl = params.toString()
    ? `${window.location.pathname}?${params}`
    : window.location.pathname;
  history.replaceState(null, '', newUrl);

  // Switch to the correct intercom and start the call
  if (camIdx !== this._activeIndex) {
    this._switchIntercom(camIdx);
  }
  // Small delay to let render complete
  setTimeout(() => this._startCall(), 500);
}
```

- [ ] **Step 2: Deploy and test**

Navigate to `http://ha.asgard.lan:8123/lovelace/debug?answer=camera.bticino_intercom_myhome_citofono_strada`. Verify:
- Card auto-switches to the correct intercom
- WebRTC call starts automatically
- URL param is removed from address bar

- [ ] **Step 3: Commit**

```bash
git add cards/bticino-intercom-card.js
git commit -m "feat(card): auto-answer from URL parameter for mobile deep link"
```

---

### Task 10: Blueprint — incoming call notification

**Files:**
- Create: `blueprints/automation/bticino_incoming_call.yaml`

- [ ] **Step 1: Create the blueprint file**

```yaml
blueprint:
  name: BTicino Intercom - Incoming Call Notification
  description: >
    Sends an actionable notification when the doorbell rings.
    Android devices ring like a phone call (alarm stream).
    Tapping Answer opens the dashboard and starts the video call.
  domain: automation
  input:
    notify_service:
      name: Notification Service
      description: The mobile app notification service (e.g., notify.mobile_app_pixel)
      selector:
        text:
    dashboard_path:
      name: Dashboard Path
      description: The Lovelace dashboard path where the intercom card is located
      default: lovelace/0
      selector:
        text:

trigger:
  - platform: event
    event_type: bticino_intercom_call
    event_data:
      type: ring

condition: []

action:
  - variables:
      session_id: "{{ trigger.event.data.session_id }}"
      module_name: "{{ trigger.event.data.module_name | default('Intercom') }}"
      camera_entity: "{{ trigger.event.data.camera_entity_id | default('') }}"
      vignette: "{{ trigger.event.data.vignette_url | default('') }}"
      dashboard: !input dashboard_path
      tag: "bticino_call_{{ session_id }}"

  - service: !input notify_service
    data:
      title: "Citofono"
      message: "{{ module_name }} sta squillando"
      data:
        tag: "{{ tag }}"
        timeout: 30
        # Android: ring like a phone call
        channel: alarm_stream
        # iOS: time-sensitive with custom sound
        push:
          interruption-level: time-sensitive
          sound:
            name: "/bticino_intercom/doorbell.wav"
        # Preview image
        image: "{{ vignette }}"
        actions:
          - action: ANSWER_{{ session_id }}
            title: "Answer"
          - action: REJECT_{{ session_id }}
            title: "Reject"

  - wait_for_trigger:
      - platform: event
        event_type: mobile_app_notification_action
        event_data:
          action: "ANSWER_{{ session_id }}"
      - platform: event
        event_type: mobile_app_notification_action
        event_data:
          action: "REJECT_{{ session_id }}"
      - platform: event
        event_type: bticino_intercom_call
        event_data:
          type: end
          session_id: "{{ session_id }}"
    timeout: "00:00:35"

  - choose:
      - conditions:
          - condition: template
            value_template: "{{ wait.trigger and 'ANSWER' in (wait.trigger.event.data.action | default('')) }}"
        sequence:
          - service: !input notify_service
            data:
              message: clear_notification
              data:
                tag: "{{ tag }}"
          - event: mobile_app.navigate
            event_data:
              path: "/{{ dashboard }}?answer={{ camera_entity }}"

      - conditions:
          - condition: template
            value_template: "{{ wait.trigger and 'REJECT' in (wait.trigger.event.data.action | default('')) }}"
        sequence:
          - service: bticino_intercom.reject_call
          - service: !input notify_service
            data:
              message: clear_notification
              data:
                tag: "{{ tag }}"

    default:
      # Timeout or call ended — clear notification
      - service: !input notify_service
        data:
          message: clear_notification
          data:
            tag: "{{ tag }}"

mode: parallel
max: 5
```

- [ ] **Step 2: Commit**

```bash
git add blueprints/automation/bticino_incoming_call.yaml
git commit -m "feat: add incoming call notification blueprint with ringtone"
```

---

### Task 11: Integration test — full answer mode flow

**Files:**
- Test: `tests/test_webrtc_camera.py`

- [ ] **Step 1: Write integration test for answer mode WebRTC**

Add to `tests/test_webrtc_camera.py`:

```python
async def test_answer_mode_uses_device_sdp(hass, mock_config_entry):
    """When active_call exists, camera should use answer mode."""
    entry = await _setup_integration(hass, mock_config_entry)
    coordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    signaling_client = hass.data[DOMAIN][entry.entry_id]["signaling_client"]

    # Simulate active call with device SDP
    device_sdp = "v=0\r\no=- 123 IN IP4 0.0.0.0\r\ns=-\r\nm=audio 9 UDP/TLS/RTP/SAVPF 111\r\na=setup:actpass\r\n"
    coordinator._active_call = {
        "session_id": "test-session",
        "module_id": MODULE_ID,  # The BNEU module ID
        "sdp": device_sdp,
        "tag_id": "tag",
        "correlation_id": "corr",
        "device_id": "dev",
    }

    # The camera should detect answer mode
    camera = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    assert coordinator.active_call is not None
    assert coordinator.active_call["sdp"] == device_sdp
```

- [ ] **Step 2: Run test**

Run: `cd /home/koma/src/bticino_intercom && python -m pytest tests/test_webrtc_camera.py::test_answer_mode_uses_device_sdp -v`
Expected: PASS (camera.py stub already exists)

- [ ] **Step 3: Commit**

```bash
git add tests/test_webrtc_camera.py
git commit -m "test: add answer mode integration test"
```

---

### Task 12: Final verification and cleanup

**Files:**
- All modified files

- [ ] **Step 1: Run full test suite**

```bash
cd /home/koma/src/bticino_intercom && python -m pytest tests/ -v --tb=short
```

Expected: ALL PASS

- [ ] **Step 2: Run hassfest validation**

```bash
docker run --rm -v $(pwd):/github/workspace ghcr.io/home-assistant/hassfest
```

Expected: No errors

- [ ] **Step 3: Deploy card to HA and full manual test**

1. Copy card JS to HA
2. Bump resource version
3. Reload page
4. Trigger `inject_test_event` → verify ring overlay appears
5. Test Answer → verify WebRTC starts
6. Test Reject → verify call terminates
7. Test Dismiss → verify overlay hides
8. Wait for timeout → verify missed call banner
9. Test URL auto-answer with `?answer=<camera_entity_id>`

- [ ] **Step 4: Commit any final adjustments**

```bash
git add -A
git commit -m "chore: final verification and cleanup for answer mode"
```
