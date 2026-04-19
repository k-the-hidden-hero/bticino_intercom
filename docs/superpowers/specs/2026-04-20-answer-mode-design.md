# Answer Mode & Incoming Call Handling — Design Spec

## Overview

Full WebRTC answer mode for the BTicino intercom integration. When someone rings the doorbell, the card shows a ring overlay with live preview and Answer/Reject/Dismiss actions. A companion blueprint sends actionable mobile notifications with ringtone.

Three usage scenarios:
1. **Card open on dashboard/display**: ring overlay appears, user chooses on screen
2. **Smart display (View Assist)**: system brings card to foreground, user chooses on display
3. **Mobile notification**: user taps "Answer" on phone → card opens and auto-connects

## 1. HA Event — Coordinator Changes

### Event: `bticino_intercom_call`

Single event name with `type` field to distinguish phases.

**Ring event** (emitted when push WebSocket delivers BNC1-rtc offer):

```json
{
  "type": "ring",
  "entry_id": "01JT3QT...",
  "module_id": "00:03:50:d9:a6:3b",
  "module_name": "Citofono Strada",
  "session_id": "abc-123",
  "snapshot_url": "/api/bticino_intercom/image/.../snapshot",
  "vignette_url": "/api/bticino_intercom/image/.../vignette",
  "camera_entity_id": "camera.bticino_intercom_casella_citofono_strada"
}
```

**End event** (emitted on rescind, terminate, or 30s timeout):

```json
{
  "type": "end",
  "reason": "rescind | terminate | timeout",
  "entry_id": "01JT3QT...",
  "module_id": "00:03:50:d9:a6:3b",
  "session_id": "abc-123"
}
```

### Where in coordinator.py

- **Ring**: in `_handle_websocket_message`, after `async_dispatcher_send(SIGNAL_CALL_RECEIVED)`, add `hass.bus.async_fire("bticino_intercom_call", {...})`. The `camera_entity_id` is derived from the module_id → entity registry lookup.
- **End**: when `rescind` or `terminate` RTC action arrives, fire the end event. Also fire on the 30s binary_sensor timeout.

### Image timing

The RTC offer event (BNC1-rtc) arrives before the status event (BNC1-incoming_call) that carries snapshot/vignette URLs. The ring event fires immediately from the RTC handler with `snapshot_url` and `vignette_url` set to `null`. When the status event arrives (typically <1s later), the coordinator fires the ring event again with image URLs populated. The card handles both: shows doorbell icon initially, swaps to preview image when the second event arrives.

## 2. Reject Call Service

### Service: `bticino_intercom.reject_call`

Registered in `__init__.py`. Accepts `entry_id` (optional, defaults to first) and `session_id` (optional, defaults to active).

Implementation:
1. Gets coordinator for the entry
2. Calls `signaling_client.send_terminate()`
3. Clears `coordinator._active_call`
4. Fires `bticino_intercom_call` end event with `reason: "rejected"`

## 3. Card — Ring Overlay

### Event Subscription

- Subscribe to `bticino_intercom_call` in `connectedCallback()` via `this._hass.connection.subscribeEvents(callback, "bticino_intercom_call")`
- Unsubscribe in `disconnectedCallback()`
- Filter events by matching `camera_entity_id` against configured intercom camera entities

### Ring UI

When `type: ring` arrives:

1. Auto-switch to the tab of the ringing intercom
2. Show ring overlay with:
   - Fresh preview image (vignette from event payload, signed via `auth/sign_path`)
   - If no image available (camera-less intercom): dark background with doorbell icon
   - Pulsating ring animation (green rings, similar to connecting overlay)
   - Intercom name displayed prominently
3. Three action buttons:
   - **Answer** (green, `mdi:phone`): starts WebRTC via `_startCall()` → camera.py handles answer mode automatically
   - **Reject** (red, `mdi:phone-hangup`): calls `bticino_intercom.reject_call` service, closes overlay
   - **Dismiss** (gray, `mdi:close`): hides overlay locally, call continues for others

### End / Missed Call

When `type: end` arrives while ring overlay is open:

- Show "Missed call" state: preview image + "Missed" badge + timestamp
- Auto-close after 5 seconds
- If user already answered (state is LIVE): disconnect WebRTC session

### Answer Mode WebRTC Flow

From the card's perspective, the flow is identical to offer mode:

1. Card calls `_startCall()` → creates PeerConnection → generates browser SDP offer
2. Sends offer to HA via `camera/webrtc/offer` (same API)
3. Camera.py detects `coordinator.active_call` exists for this module → answer mode:
   - Transforms browser offer: `setup:actpass` → `setup:active`
   - Sends transformed offer as answer to device via `signaling.send_answer()`
   - Returns device's original SDP offer to browser as the "answer"
4. Browser sets remote description, media flows

The card does NOT need to know whether it's in offer or answer mode. Camera.py handles the routing.

## 4. Card — URL Auto-Answer

For mobile notification deep linking:

- Card checks `window.location.search` on load
- If `?answer=<camera_entity_id>` is present:
  1. Switch to the matching intercom tab
  2. Skip ring overlay
  3. Call `_startCall()` directly (answer mode)
  4. Remove param with `history.replaceState()`

## 5. Card — Idle Poster Change

Remove cached poster image in idle state:

- **Before**: shows last captured frame (misleading, looks like live feed)
- **After**: dark background with centered camera/doorbell icon
- Click on video area still triggers offer mode connection
- Remove `_updatePoster()` periodic fetch and poster `<img>` element

## 6. Blueprint — Incoming Call Notification

File: `blueprints/automation/incoming_call_notification.yaml`

### Trigger

Event `bticino_intercom_call` with `type: ring`.

### Actions

1. Send actionable notification:
   - **Android**: `channel: alarm_stream` (rings like phone call, overrides DND), `timeout: 30`
   - **iOS**: `push.interruption-level: time-sensitive`, custom sound
   - **Image**: vignette URL from event payload
   - **Actions**: Answer, Reject

2. Wait for user response or call end:
   - If `ANSWER`: navigate to `/lovelace/<dashboard>?answer={{ camera_entity_id }}`
   - If `REJECT`: call `bticino_intercom.reject_call`
   - If `bticino_intercom_call` end event arrives first: clear notification via `tag: bticino_call_{{ session_id }}`

All notifications use tag `bticino_call_<session_id>` for idempotent updates and cancellation.

### Ringtone

File: `custom_components/bticino_intercom/www/doorbell.wav`

Registered via `hass.http.register_static_path("/bticino_intercom", www_path)` in `__init__.py`. Blueprint references `/bticino_intercom/doorbell.wav` for iOS sound. Android uses `alarm_stream` channel which uses the system alarm sound.

### Blueprint Inputs

- `notify_service`: which notify service to use (e.g., `notify.mobile_app_pixel`)
- `dashboard_path`: lovelace dashboard path (default: `lovelace/0`)

## 7. Scope

### In scope
- Coordinator event emission (ring/end)
- `reject_call` service
- Card ring overlay (preview, Answer/Reject/Dismiss)
- Card missed call display (5s auto-close)
- Card URL auto-answer param
- Card idle poster removal
- Camera.py answer mode testing (stub exists)
- Blueprint with ringtone
- Doorbell sound file in integration

### Out of scope
- Reverse call (phone → intercom) — separate feature, needs API discovery
- Firefox support — firmware limitation
- Multiple simultaneous calls — single active call per entry
