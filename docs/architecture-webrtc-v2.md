# BTicino Intercom v2.0 — WebRTC Architecture & Reference Guide

> **Purpose**: Comprehensive architectural reference for implementing WebRTC live video,
> event entity migration, and two-way audio in the BTicino Intercom integration.
> Written to be self-contained — a new session can pick this up without prior context.
>
> **Date**: 2026-04-17 (updated with confirmed findings)
> **Branch**: `dev/webrtc` (based on `main` v1.9.2)
> **Status**: WebRTC live video working (offer mode). Event entity done. Two-way audio mechanism confirmed. Release pending.

---

## Table of Contents

1. [Current State](#1-current-state)
2. [Key Findings](#2-key-findings)
3. [Industry Research — How Others Do It](#3-industry-research)
4. [Recommended Architecture (Ring+Nest+UniFi Mix)](#4-recommended-architecture)
5. [WebRTC Implementation Guide](#5-webrtc-implementation-guide)
6. [Event Entity Migration](#6-event-entity-migration)
7. [Two-Way Audio](#7-two-way-audio)
8. [SDP Answer Fixing](#8-sdp-answer-fixing)
9. [Session Management](#9-session-management)
10. [ICE / TURN / STUN](#10-ice-turn-stun)
11. [Code References](#11-code-references)
12. [Remaining Work Checklist](#12-remaining-work-checklist)
13. [References](#13-references)

---

## 1. Current State

### What exists (v1.9.2 on `main`)

- **5 platforms**: lock, binary_sensor, sensor, light, camera (snapshot/vignette only)
- **WebSocket push** for real-time RTC events (call/rescind/terminate) + 5-min polling
- **Token persistence** via HA Store (avoids login emails on restart)
- **Reauth flow**, diagnostics endpoint, quality_scale.yaml
- **92 tests** passing

### What exists on `dev/webrtc` (v2.0.0-rc1)

- `SignalingClient` in pybticino — connects to `wss://app-ws.netatmo.net/appws/`
- Camera entity with full `async_handle_async_webrtc_offer` (ICE buffering, error handling, terminate)
- Enhanced coordinator event processing (RTC offer/rescind/terminate + status events)
- **Event entity** (`event.py`) with `EventDeviceClass.DOORBELL` alongside binary_sensor
- **107 tests green**, lint clean, full mock_setup_entry fixture
- **WebRTC live video working** in offer mode (on-demand viewing)
- **Multi-camera support**: one camera entity per BNEU external unit module
- **Per-module call session tracking** with retransmission deduplication
- **Real-time snapshot/vignette** from `incoming_call` push events
- SDP manipulation methods exist in camera.py but are disabled (audio hacks not needed)

### Key discoveries from reverse engineering

- **Signaling WS URL**: `wss://app-ws.netatmo.net/appws/` (NOT `app.netatmo.net` — that redirects)
- **Ack format**: `{type: "ack", session_id, tag_id, correlation_id, status: "ok"}`
- **Terminate with error**: `{type: "rtc", data: {type: "terminate", error: {code: 1, message: "..."}}}`
- **TURN endpoint**: `/turn` returns 404 — correct path not yet found (not blocking for LAN)
- **Session timeout**: ~30s in offer mode (device-side behavior, not configurable)
- **Device mic activation**: requires real RTP audio packets, not just SDP `sendrecv`
- **Answer mode vs offer mode**: different signaling flows (see pybticino docs for details)

### Repository structure

```
bticino_intercom/
├── custom_components/bticino_intercom/
│   ├── __init__.py      — Entry setup, WS manager, token persistence
│   ├── coordinator.py   — DataUpdateCoordinator, WS event processing
│   ├── camera.py        — Snapshot/vignette + WebRTC WIP
│   ├── config_flow.py   — Setup + reauth flow
│   ├── diagnostics.py   — Redacted debug export
│   ├── entity.py        — Base entity class
│   ├── lock.py          — Door locks (BNDL modules)
│   ├── binary_sensor.py — Call sensor (BNEU modules)
│   ├── sensor.py        — Last call, call count, bridge status
│   ├── light.py         — Staircase lights (BNSL modules)
│   ├── const.py         — Module subtypes, event types, platform mappings
│   └── utils.py         — Timestamp/uptime formatting
├── tests/               — 92 tests (coordinator, camera, token persistence, integration)
└── docs/                — This document
```

---

## 2. Key Findings

These are confirmed findings from real device testing and reverse engineering, documented here for future reference.

### Device mic requires real RTP packets

The BTicino device microphone does not activate based on SDP negotiation alone. Even with `sendrecv` direction in the audio m-line, the device will not send audio from its microphone unless it receives actual RTP audio packets from the peer. This means the browser must send a real audio track (not silence, not empty). The solution is a custom Lovelace card that creates an `AudioContext` with an oscillator to generate a minimal audio signal.

See: [pybticino WebRTC Audio documentation](https://github.com/k-the-hidden-hero/pybticino/blob/main/docs/webrtc-audio.md)

### Session timeout ~30s in offer mode

When the HA client initiates a WebRTC session (offer mode / on-demand viewing), the device terminates the session after approximately 30 seconds. This is device-side behavior and cannot be extended from the client side. The user must re-open the stream to continue viewing. This timeout does not apply in answer mode (incoming call).

### HA built-in camera player mutes audio

Home Assistant's built-in WebRTC camera player mutes audio by default. This is by design (HA core PR #25767) to avoid unexpected audio playback. A custom Lovelace card is needed to enable audio playback and microphone input. The companion project [bticino_ha_extras](https://github.com/k-the-hidden-hero/bticino_ha_extras) provides this card.

### Custom Lovelace card needed for audio

Because of the HA audio muting and the device's RTP requirement, full two-way audio requires the `bticino_ha_extras` custom card, which:
- Unmutes the audio output from the WebRTC stream
- Creates an `AudioContext` oscillator to send real audio data
- Enables microphone access for talk-back

### Multi-camera: one entity per BNEU module

Each BNEU (external unit) module in the BTicino topology gets its own camera entity. This allows homes with multiple entrance cameras to view each independently. The entity unique ID includes the module ID for deduplication.

### Answer mode vs offer mode signaling

Two distinct WebRTC signaling flows exist:
- **Offer mode** (on-demand): HA sends SDP offer to device via Netatmo cloud, device responds with SDP answer. Used for live viewing.
- **Answer mode** (incoming call): device sends SDP offer via push WebSocket when someone rings. HA (or the app) responds with an SDP answer to join the call.

The current integration implements offer mode. Answer mode is documented but not yet implemented in the camera entity.

See: [pybticino WebRTC Signaling documentation](https://github.com/k-the-hidden-hero/pybticino/blob/main/docs/webrtc-signaling.md)

---

## 3. Industry Research

Analyzed 5 major doorbell/intercom integrations in HA core to identify best patterns.

### 3.1 Ring (`homeassistant/components/ring/`)

**Why it matters**: Closest architectural match to BTicino — cloud-only, WebSocket signaling, native WebRTC.

| Aspect | Implementation |
|---|---|
| **Event detection** | Firebase Cloud Messaging (FCM) via `ring_doorbell.RingEventListener` |
| **Video** | Native WebRTC via dedicated WebSocket (`wss://` to Ring's streaming servers) |
| **Two-way audio** | Yes — `audio_enabled: True` in WebRTC stream options, sendrecv SDP |
| **Doorbell entity** | `event` entity with `EventDeviceClass.DOORBELL` (migrated from binary_sensor in 2025.4) |
| **Call lifecycle** | No answer/hangup in HA — video is on-demand, independent from ring event |
| **Coordinators** | Two: `RingDataCoordinator` (polling 60s) + `RingListenCoordinator` (FCM push, lazy-started) |

**WebRTC signaling flow** (in `ring_doorbell.webrtcstream.RingWebRtcStream`):
1. POST to `RTC_STREAMING_TICKET_ENDPOINT` → get ticket
2. Open WebSocket to `RTC_STREAMING_WEB_SOCKET_ENDPOINT` with ticket
3. Send `live_view` message with SDP offer, `doorbot_id`, stream options
4. Receive: `session_created` → `sdp` answer → `ice` candidates → `camera_started`
5. Send `activate_session`
6. Keep alive with `ping`/`pong` every 5s

**SDP fixing**: `force_correct_sdp_answer()` — Ring returns `sendrecv` instead of `sendonly` for `recvonly` offers. Fixed client-side.

**Key file references**:
- `camera.py` — `async_handle_async_webrtc_offer`, `async_on_webrtc_candidate`, `close_webrtc_session`
- `event.py` — `RingEvent` entity with `EventDeviceClass.DOORBELL`
- Library: `ring_doorbell` on PyPI

### 3.2 Google Nest (`homeassistant/components/nest/`)

**Why it matters**: Clean WebRTC implementation, good session management, vanilla ICE pattern.

| Aspect | Implementation |
|---|---|
| **Event detection** | Google Cloud Pub/Sub (gRPC streaming pull) |
| **Video** | WebRTC (via REST API) or RTSP (per device capability) |
| **Two-way audio** | SDP supports sendrecv for audio, not explicitly surfaced |
| **Doorbell entity** | `event` entity with `EventDeviceClass.DOORBELL` via `DoorbellChimeTrait` |
| **Session refresh** | `ExtendWebRtcStream` API call before expiration, backoff on failure |

**WebRTC signaling flow** (simpler than Ring — REST, not WebSocket):
1. Send SDP offer via `CameraLiveStream.GenerateWebRtcStream` API command
2. Receive SDP answer + `mediaSessionId` + `expiresAt`
3. Run `fix_sdp_answer()` on the response
4. Send `WebRTCAnswer(answer_sdp)` to frontend
5. Extend session before expiry via `ExtendWebRtcStream`
6. Stop via `StopWebRtcStream`

**Vanilla ICE**: `async_on_webrtc_candidate` is a **no-op** — all ICE candidates bundled in SDP. Simplifies implementation.

**Session management**:
```python
self._webrtc_sessions: dict[str, WebRtcStream] = {}
# On offer: self._webrtc_sessions[session_id] = stream
# On close: stream.stop_stream(), del self._webrtc_sessions[session_id]
```

**Stream refresh** (`StreamRefresh` class):
- Schedules refresh before `expiresAt`
- On failure: exponential backoff (1min → ×1.5 → max 10min)
- On success: reschedules for next expiry

**Client configuration**:
```python
def _async_get_webrtc_client_configuration(self) -> WebRTCClientConfiguration:
    return WebRTCClientConfiguration(data_channel="dataSendChannel")
```
No custom STUN/TURN — Google bundles relay candidates in the SDP answer.

**Key file references**:
- `camera_sdm.py` — `NestWebRTCEntity`, `NestRTSPEntity`
- `event.py` — `NestTraitEventEntity`
- Library: `google-nest-sdm` on PyPI, especially `webrtc_util.py` for SDP fixing

### 3.3 UniFi Protect (`homeassistant/components/unifiprotect/`)

**Why it matters**: Best event lifecycle handling, device-driven reset, dual entity pattern.

| Aspect | Implementation |
|---|---|
| **Event detection** | Local WebSocket to NVR (`uiprotect.ProtectApiClient`) |
| **Video** | RTSP → go2rtc → WebRTC (no custom WebRTC code) |
| **Two-way audio** | `media_player` entity with `device.play_audio()` (one-way to speaker) |
| **Doorbell entity** | Both `binary_sensor` AND `event` entity (dual exposure) |
| **Event reset** | Driven by NVR — `event.end` timestamp set by device, no hardcoded timeout |

**Event deduplication**: `_event_already_ended()` checks both event object identity AND end timestamp to handle in-place mutation by the library.

**Dual entity pattern** (for migration):
- `binary_sensor` with `BinarySensorDeviceClass.OCCUPANCY` for `doorbell` — existing, for backwards compat
- `event` entity with `EventDeviceClass.DOORBELL` — new, modern pattern
- Both fire from the same WebSocket event

**Key file references**:
- `binary_sensor.py` — `ProtectEventBinarySensor`, `_event_already_ended()`
- `event.py` — `ProtectDeviceRingEventEntity`
- `media_player.py` — `ProtectMediaPlayer` (talkback speaker)
- `data.py` — `ProtectData` (custom coordinator with MAC-based subscriptions)

### 3.4 Reolink (`homeassistant/components/reolink/`)

**Why it matters**: Multi-fallback event detection, entity description pattern.

| Aspect | Implementation |
|---|---|
| **Event detection** | 4-level fallback: TCP Baichuan → ONVIF webhook → ONVIF long poll → fast poll 5s |
| **Video** | RTSP/RTMP → go2rtc (no custom WebRTC) |
| **Two-way audio** | Not implemented in HA |
| **Doorbell entity** | `binary_sensor` only (no event entity) |
| **Entity pattern** | `ReolinkBinarySensorEntityDescription` dataclass with `supported` lambdas |

Less relevant for our architecture but notable for resilient push event handling.

### 3.5 Netatmo (`homeassistant/components/netatmo/`)

**Why it matters**: Same backend API as BTicino, but their integration is basic.

| Aspect | Implementation |
|---|---|
| **Event detection** | HTTP webhook (requires HTTPS or Nabu Casa) + polling 60s |
| **Video** | HLS (local or cloud VPN tunnel) — no WebRTC |
| **Doorbell entity** | No event entity, no binary_sensor for doorbell. Only bus events + device triggers |
| **Doorbell streaming** | NDB devices explicitly excluded from `CameraEntityFeature.STREAM` |

**Key insight**: Netatmo's official integration doesn't support BTicino doorbell streaming at all.
Their doorbells (`NDB`) get camera entities for snapshots only. We're building what they haven't.

**Webhook vs our WebSocket**: Netatmo uses `auth.async_addwebhook(webhook_url)` for push events.
This requires a publicly accessible HTTPS URL on port 443, or Nabu Casa cloudhook.
Our WebSocket approach is superior — works behind NAT, no public URL needed.

### 3.6 Summary Comparison Table

| | Ring | Nest | UniFi | Reolink | Netatmo | **BTicino** |
|---|---|---|---|---|---|---|
| Push mechanism | FCM | Pub/Sub | Local WS | TCP/ONVIF | Webhook | **Cloud WS** |
| WebRTC native | ✅ | ✅ | ❌ go2rtc | ❌ go2rtc | ❌ | **✅ WIP** |
| Two-way audio | ✅ WebRTC | ⚠️ SDP | ⚠️ media_player | ❌ | ❌ | **🎯** |
| Event entity | ✅ | ✅ | ✅ + binary | ❌ | ❌ | **🎯** |
| Call answer in HA | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |
| SDP fixing | ✅ | ✅ | N/A | N/A | N/A | **🎯** |

**Universal pattern**: No integration implements call answering/rejecting in HA.
Video is always on-demand. The doorbell ring is just a notification trigger.

---

## 4. Recommended Architecture (Ring+Nest+UniFi Mix)

### Design principle

```
Ring   (80%) — WebRTC signaling pattern, event entity, two-way audio via WebRTC
Nest   (15%) — Session management, vanilla ICE fallback, minimal client config
UniFi   (5%) — Device-driven event reset, dual entity during migration
```

### Why this mix

| Component | Source | Rationale |
|---|---|---|
| WebRTC signaling | **Ring** | Both use WebSocket for signaling (ours: Netatmo WS, Ring: dedicated WS). Same pattern: send offer via WS, receive answer via WS |
| Session tracking | **Nest** | Simpler than Ring. Dict of `{session_id: stream}`, refresh before expiry, clean stop |
| ICE handling | **Nest first, Ring fallback** | Try vanilla ICE (candidates in SDP). If Netatmo doesn't bundle them, switch to trickle ICE via WS like Ring |
| Event entity | **Ring** | Clean migration from binary_sensor to EventDeviceClass.DOORBELL. Ring did this in 2025.4 |
| Event lifecycle | **UniFi** | Use `terminate` WS event as primary reset. Keep 30s timeout as safety net only |
| Two-way audio | **Ring** | `audio_enabled: True` in WebRTC, sendrecv in SDP. Natural part of the stream |
| SDP fixing | **Ring + Nest** | Both do it. Write our own `fix_sdp_answer()` based on observed Netatmo responses |
| Client config | **Nest** | Minimal `WebRTCClientConfiguration`. Don't over-configure STUN/TURN |

### What NOT to copy

- **Netatmo's webhook approach** — inferior to our WebSocket
- **Reolink's 4-level fallback** — over-engineered for our case
- **UniFi's media_player talkback** — workaround for no WebRTC; we'll have native WebRTC
- **Ring's FCM push** — we already have WebSocket push, which is better
- **Nest's REST signaling** — our signaling is WebSocket-based (like Ring)

---

## 5. WebRTC Implementation Guide

### 5.1 Camera entity structure

Follow Ring's pattern. The camera entity must override three methods:

```python
class BticinoWebRTCCamera(Camera):
    """BTicino camera with WebRTC live video."""

    _attr_supported_features = CameraEntityFeature.STREAM

    def __init__(self, ...):
        self._webrtc_sessions: dict[str, SignalingSession] = {}

    async def async_handle_async_webrtc_offer(
        self, offer_sdp: str, session_id: str, send_message: WebRTCSendMessage
    ) -> None:
        """Handle a WebRTC offer and return answer via callback."""
        # 1. Send offer to Netatmo via SignalingClient
        # 2. Receive answer SDP
        # 3. Fix SDP if needed
        # 4. send_message(WebRTCAnswer(answer_sdp))
        # 5. Track session: self._webrtc_sessions[session_id] = session
        pass

    async def async_on_webrtc_candidate(
        self, session_id: str, candidate: RTCIceCandidateInit
    ) -> None:
        """Handle trickle ICE candidate."""
        # If vanilla ICE: return (no-op, like Nest)
        # If trickle ICE: forward to SignalingClient via WS (like Ring)
        pass

    def close_webrtc_session(self, session_id: str) -> None:
        """Close a WebRTC session."""
        # 1. Send terminate to SignalingClient
        # 2. Clean up: del self._webrtc_sessions[session_id]
        pass
```

**Important**: HA auto-detects WebRTC support by checking if `async_handle_async_webrtc_offer`
is overridden (camera `__init__.py` lines 461-462 in HA core). Just override it and set
`CameraEntityFeature.STREAM`.

### 5.2 Signaling flow (via pybticino SignalingClient)

Our flow mirrors Ring's but uses Netatmo's WebSocket:

```
Browser                  HA (camera.py)              pybticino              Netatmo WS
   |                          |                     SignalingClient              |
   |-- WebRTC offer --------->|                          |                      |
   |                          |-- send_offer(sdp) ------>|                      |
   |                          |                          |-- WS: {type:"rtc",   |
   |                          |                          |    data:{type:"offer",|
   |                          |                          |    sdp:...}} -------->|
   |                          |                          |                      |
   |                          |                          |<-- WS: {type:"ack"}  |
   |                          |                          |<-- WS: {type:"rtc",  |
   |                          |                          |    data:{type:"answer"|
   |                          |                          |    sdp:...}}          |
   |                          |<-- answer_sdp -----------|                      |
   |                          |-- fix_sdp_answer() ----->|                      |
   |<-- WebRTCAnswer(sdp) ----|                          |                      |
   |                          |                          |                      |
   |====== WebRTC media flows directly browser <-> Netatmo TURN/STUN =========|
```

### 5.3 What to implement in pybticino vs bticino_intercom

| Layer | Component | Responsibility |
|---|---|---|
| **pybticino** | `SignalingClient` | WebSocket connection to `appws/`, send offer, receive answer, send/receive ICE, send terminate |
| **pybticino** | `WebRtcStream` (new) | Dataclass holding session_id, answer_sdp, expires_at, media_session_id |
| **bticino_intercom** | `camera.py` | HA WebRTC protocol methods, session tracking dict, SDP fixing |
| **bticino_intercom** | `coordinator.py` | Active call state, dispatch SIGNAL_CALL_RECEIVED for event entity |

---

## 6. Event Entity Migration

### 6.1 Current state

Doorbell ring is a `binary_sensor` in `binary_sensor.py` that:
- Turns ON when an RTC `call` event is received via WebSocket
- Turns OFF after 30s timeout or when `terminate` is received
- Uses `SIGNAL_CALL_RECEIVED` dispatcher signal

### 6.2 Target state (following Ring's pattern)

Add an `event` entity alongside the binary_sensor:

```python
# event.py (new file)
from homeassistant.components.event import EventDeviceClass, EventEntity

class BticinoDoorbellEvent(BticinoEntity, EventEntity):
    """Event entity for doorbell ring."""

    _attr_device_class = EventDeviceClass.DOORBELL
    _attr_event_types = ["ring"]

    async def async_added_to_hass(self) -> None:
        """Register for call events."""
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                SIGNAL_CALL_RECEIVED,
                self._handle_call_event,
            )
        )

    @callback
    def _handle_call_event(self, event_data: dict) -> None:
        """Handle incoming call."""
        self._trigger_event("ring", event_data)
        self.async_write_ha_state()
```

### 6.3 Migration strategy (following UniFi's dual pattern)

1. **v2.0**: Add `event` entity, keep `binary_sensor` — both fire from same WS event
2. **v2.1**: Deprecate `binary_sensor` with HA repair issue (like Ring did in 2025.4)
3. **v2.2**: Remove `binary_sensor`

### 6.4 Event lifecycle improvement (from UniFi)

Current: 30s hardcoded timeout for binary_sensor auto-off.
Improved:
- **Primary reset**: `terminate` WebSocket event → immediate off
- **Safety net**: 30s timeout only if `terminate` is never received (network issue)
- **`rescind` event**: Also resets the sensor (call cancelled before answer)

---

## 7. Two-Way Audio

### 7.1 How Ring does it

Two-way audio is NOT a separate feature — it's part of the WebRTC stream:
- The `stream_options` include `audio_enabled: True`
- The browser's SDP offer includes an audio track with `sendrecv` direction
- Ring's SDP answer includes `sendrecv` for audio
- Result: bidirectional audio via standard WebRTC

### 7.2 How to implement for BTicino

1. Ensure the SDP offer from the browser includes audio `sendrecv`
   (HA frontend does this when the camera supports `CameraEntityFeature.STREAM`)
2. Pass the full offer (including audio) to Netatmo signaling
3. Netatmo's answer should include audio capabilities
4. If the answer has `sendrecv` for audio → two-way audio works automatically
5. If not → we may need to modify the SDP or it may be a device limitation

### 7.3 Confirmed behavior (from real device testing)

The device **does** negotiate `sendrecv` for audio in the SDP answer, but its microphone only activates when it receives real RTP audio packets. SDP negotiation alone is not sufficient.

**What works**:
- Receiving audio from the device (hearing the visitor) works once the device mic is activated
- Sending audio from the browser to the device speaker works via standard WebRTC

**What is needed**:
- A custom Lovelace card (`bticino_ha_extras`) that uses an `AudioContext` oscillator to send a minimal audio signal, which triggers the device mic
- HA's built-in camera player mutes audio (by design), so the custom card is also needed to unmute playback

**SDP hacks disabled**: `camera.py` contains SDP manipulation methods (direction forcing, codec reordering) that were explored during development. These are currently disabled because the root cause is not SDP-related but rather the device's RTP packet requirement.

See: [pybticino WebRTC Audio documentation](https://github.com/k-the-hidden-hero/pybticino/blob/main/docs/webrtc-audio.md)

---

## 8. SDP Answer Fixing

### 8.1 Why it's needed

Both Ring and Nest fix the SDP answer from the cloud. Common issues:

1. **Direction mismatch**: Cloud returns `sendrecv` for tracks where browser offered `recvonly`.
   RFC 3264 says the answer should be `sendonly` for `recvonly` offers.
2. **Codec ordering**: Cloud may reorder codecs in a way browsers don't like.
3. **Missing attributes**: Some required SDP attributes may be missing.

### 8.2 Reference implementations

**Ring** (`ring_doorbell/webrtcstream.py`):
```python
def force_correct_sdp_answer(sdp: str) -> str:
    # Fixes sendrecv -> sendonly for recvonly offers
```

**Nest** (`google-nest-sdm/webrtc_util.py`):
```python
def fix_sdp_answer(answer: str, offer: str) -> str:
    # Matches direction in answer to what offer requested
```

### 8.3 Our implementation plan

1. First, capture raw SDP answers from Netatmo signaling (log them)
2. Test without fixing — may work as-is
3. If browser rejects the answer, implement `fix_sdp_answer()` based on observed issues
4. Keep it minimal — only fix what's actually broken

---

## 9. Session Management

### 9.1 Pattern (from Nest)

```python
class BticinoWebRTCCamera(Camera):
    def __init__(self, ...):
        self._webrtc_sessions: dict[str, WebRtcStream] = {}

    async def async_handle_async_webrtc_offer(self, offer_sdp, session_id, send_message):
        stream = await self._signaling.send_offer(offer_sdp)
        self._webrtc_sessions[session_id] = stream
        send_message(WebRTCAnswer(stream.answer_sdp))
        # Schedule refresh if stream has expiration
        if stream.expires_at:
            self._schedule_refresh(session_id, stream)

    def close_webrtc_session(self, session_id: str) -> None:
        if stream := self._webrtc_sessions.pop(session_id, None):
            # Send terminate to Netatmo
            self.hass.async_create_task(self._signaling.terminate(stream))

    async def async_will_remove_from_hass(self) -> None:
        # Close all active sessions on entity removal
        for session_id in list(self._webrtc_sessions):
            self.close_webrtc_session(session_id)
```

### 9.2 Session refresh (from Nest's StreamRefresh)

Netatmo WebRTC sessions likely have a timeout. Implement refresh:
- Track `expires_at` per session
- Schedule `async_call_later` to refresh before expiry
- On refresh failure: exponential backoff (1min, 1.5x, max 10min)
- On success: reschedule

---

## 10. ICE / TURN / STUN

### 10.1 Strategy: try vanilla ICE first (Nest pattern)

```python
async def async_on_webrtc_candidate(self, session_id, candidate):
    """Ignore trickle ICE — candidates bundled in SDP."""
    return
```

If this doesn't work (browser can't establish media), switch to trickle ICE (Ring pattern):
```python
async def async_on_webrtc_candidate(self, session_id, candidate):
    if stream := self._webrtc_sessions.get(session_id):
        await self._signaling.send_ice_candidate(stream, candidate)
```

### 10.2 TURN server discovery

Current state: `/turn` endpoint returns 404.

Options to investigate:
1. Check APK strings more carefully for the correct TURN endpoint
2. Capture actual WebRTC traffic from the BTicino app to see what TURN servers it uses
3. Check if TURN candidates are bundled in Netatmo's SDP answer
4. If Netatmo provides TURN in the SDP → we don't need to discover the endpoint at all

### 10.3 Client configuration

```python
def _async_get_webrtc_client_configuration(self) -> WebRTCClientConfiguration:
    # Start minimal (like Nest):
    return WebRTCClientConfiguration()
    # If TURN servers needed later:
    # return WebRTCClientConfiguration(
    #     configuration=RTCConfiguration(
    #         ice_servers=[RTCIceServer(urls=["turn:..."], username="...", credential="...")]
    #     )
    # )
```

---

## 11. Code References

### 11.1 HA Core WebRTC infrastructure

These are the HA core classes our camera entity must interact with:

| Class | Location | Purpose |
|---|---|---|
| `Camera` | `homeassistant/components/camera/__init__.py` | Base class, override `async_handle_async_webrtc_offer` |
| `WebRTCAnswer` | `homeassistant/components/camera/webrtc.py` | Wrap SDP answer for `send_message()` |
| `WebRTCCandidate` | `homeassistant/components/camera/webrtc.py` | Wrap ICE candidate for `send_message()` |
| `WebRTCError` | `homeassistant/components/camera/webrtc.py` | Send error to frontend |
| `WebRTCSendMessage` | `homeassistant/components/camera/webrtc.py` | Callback type for async messaging |
| `WebRTCClientConfiguration` | `homeassistant/components/camera/webrtc.py` | STUN/TURN config for browser |
| `RTCIceCandidateInit` | `homeassistant/components/camera/webrtc.py` | ICE candidate from browser |
| `CameraEntityFeature.STREAM` | `homeassistant/components/camera/__init__.py` | Feature flag to enable streaming |
| `EventEntity` | `homeassistant/components/event/__init__.py` | Base class for event entities |
| `EventDeviceClass.DOORBELL` | `homeassistant/components/event/__init__.py` | Device class for doorbell events |

### 11.2 Reference integration code to study

**Ring WebRTC** (most relevant):
```
homeassistant/components/ring/camera.py     — async_handle_async_webrtc_offer
ring_doorbell/webrtcstream.py               — RingWebRtcStream (signaling via WS)
ring_doorbell/webrtcstream.py               — force_correct_sdp_answer
```

**Nest WebRTC** (session management):
```
homeassistant/components/nest/camera_sdm.py — NestWebRTCEntity
google_nest_sdm/camera_traits.py            — generate_web_rtc_stream
google_nest_sdm/webrtc_util.py              — fix_sdp_answer, StreamRefresh
```

**Ring Event Entity** (migration pattern):
```
homeassistant/components/ring/event.py      — RingEvent with EventDeviceClass.DOORBELL
homeassistant/components/ring/binary_sensor.py — deprecated, async_check_create_deprecated
```

**UniFi Event Lifecycle**:
```
homeassistant/components/unifiprotect/binary_sensor.py — _event_already_ended
homeassistant/components/unifiprotect/event.py         — dual event+binary_sensor
```

### 11.3 Our codebase (current key files)

| File | Lines | Key content |
|---|---|---|
| `__init__.py` | ~370 | WS manager, token persistence, SignalingClient setup |
| `coordinator.py` | ~470 | `_process_websocket_event()`, `_handle_websocket_message()` |
| `camera.py` | ~460 | Snapshot/vignette + WebRTC WIP (on dev/webrtc) |
| `binary_sensor.py` | ~280 | Call sensor — will add event entity alongside |
| `const.py` | ~70 | `SIGNAL_CALL_RECEIVED`, module subtypes |

### 11.4 pybticino (our library)

| File | Key content |
|---|---|
| `auth.py` | `AuthHandler` with `token_callback` + `set_tokens()` (v1.6.0) |
| `websocket.py` | `WebsocketClient` — persistent WS for push events |
| `signaling.py` | `SignalingClient` — WS to `appws/` for WebRTC signaling (dev/webrtc) |
| `account.py` | `AsyncAccount` — REST API (topology, status, events) |

---

## 12. Remaining Work Checklist

> **Last updated**: 2026-04-17

### Phase 1: WebRTC Live Video (core) -- DONE

- [x] ~~Test WebRTC offer with free device (no other active sessions)~~ (done: video live works)
- [x] ~~Capture and log raw SDP answer from Netatmo~~ (done: documented in pybticino)
- [x] ~~Implement `async_handle_async_webrtc_offer` fully in camera.py~~ (done: camera.py with full signaling flow, ICE buffering, error handling)
- [x] ~~Determine ICE strategy (vanilla vs trickle) based on Netatmo's SDP answer~~ (done: ICE timing fixed)
- [x] ~~Implement `close_webrtc_session` with terminate signaling~~ (done: sends terminate via SignalingClient)
- [x] ~~Add session tracking~~ (done: via SignalingClient session_id)
- [x] ~~Test end-to-end: browser -> HA -> Netatmo -> video playing~~ (done: offer mode working)

See [pybticino WebRTC Signaling documentation](https://github.com/k-the-hidden-hero/pybticino/blob/main/docs/webrtc-signaling.md) for protocol details.

### Phase 2: Robustness -- PARTIAL

- [x] ~~Implement SDP answer fixing if needed~~ (done: SDP hacks explored and disabled -- not needed for basic flow)
- [ ] Add session refresh/keepalive mechanism (not possible -- device terminates at ~30s in offer mode)
- [x] ~~Handle `WebRTCError` for error cases~~ (done: on_event callback sends WebRTCError to frontend)
- [x] ~~ICE timing fixed~~ (done: resolved ICE candidate delivery timing)
- [x] ~~Session cleanup works~~ (done: terminate sent on close)
- [ ] Find correct TURN endpoint (returns 404 -- not blocking for LAN, investigate for remote access)

### Phase 3: Event Entity -- DONE

- [x] ~~Create `event.py` with `BticinoDoorbellEvent` (EventDeviceClass.DOORBELL)~~ (done: 2026-04-01)
- [x] ~~Wire to `SIGNAL_CALL_RECEIVED` dispatcher~~ (done: fires "ring" on state=True)
- [x] ~~Add to PLATFORMS in const.py~~ (done: Platform.EVENT added)
- [x] ~~Keep binary_sensor for backwards compat (dual entity like UniFi)~~ (done: both coexist)
- [ ] Use `terminate` WS event as primary reset for binary_sensor (currently uses 30s timeout)

### Phase 4: Two-Way Audio -- CONFIRMED

- [x] ~~Test if Netatmo's SDP answer includes sendrecv for audio~~ (confirmed: yes, device negotiates sendrecv)
- [x] ~~Determine audio activation mechanism~~ (confirmed: device mic requires real RTP audio packets)
- [x] ~~Solution identified~~ (confirmed: custom card with AudioContext oscillator, see bticino_ha_extras)
- [x] ~~SDP manipulation methods disabled~~ (done: audio hacks not needed, root cause is RTP not SDP)

See [pybticino WebRTC Audio documentation](https://github.com/k-the-hidden-hero/pybticino/blob/main/docs/webrtc-audio.md) for the full analysis.

### Phase 5: Release -- PENDING

- [x] ~~Bump to v2.0.0-rc1~~ (done: pre-release tag)
- [ ] Update README with WebRTC/live video documentation
- [ ] Test on production HA (dev tag first, then final)
- [ ] Final v2.0.0 release

### Test Infrastructure (added 2026-04-01)

- [x] ~~`mock_setup_entry` fixture in conftest.py~~ (done: full integration setup with all mocks)
- [x] ~~107 tests green, lint clean~~ (done: coordinator, camera, binary_sensor, event, sensor, lock, light, integration, config_flow, init, token_persistence, webrtc_camera)
- [x] ~~Event entity tests~~ (done: 4 tests in test_event.py)
- [x] ~~WebRTC camera tests~~ (done: 3 tests in test_webrtc_camera.py)

---

## Appendix: Netatmo API Details (from reverse engineering)

### WebSocket endpoints

| Purpose | URL | Notes |
|---|---|---|
| Push events | `wss://app-ws.netatmo.net/ws/` | Persistent, for RTC call/rescind/terminate |
| Signaling | `wss://app-ws.netatmo.net/appws/` | For WebRTC offer/answer exchange |

### RTC event structure

```json
// Incoming call
{"push_type": "...", "event_list": [...], "extra_params": {
  "module_id": "...",
  "data": {"type": "call", "session_id": "...", "session_description": {...}}
}}

// Call cancelled
{"data": {"type": "rescind", "session_id": "..."}}

// Call ended
{"data": {"type": "terminate", "session_id": "..."}}

// Terminate with error
{"type": "rtc", "data": {"type": "terminate", "error": {"code": 1, "message": "Max peers"}}}
```

### Signaling message structure (WebRTC)

```json
// Send offer
{"type": "rtc", "data": {"type": "offer", "sdp": "...", "session_id": "..."}}

// Receive ack
{"type": "ack", "session_id": "...", "tag_id": "...", "correlation_id": "...", "status": "ok"}

// Receive answer
{"type": "rtc", "data": {"type": "answer", "sdp": "..."}}

// ICE candidate (if trickle)
{"type": "rtc", "data": {"type": "ice", "candidate": "..."}}
```

### Test credentials

Stored in `.credentials` file (gitignored). Dev account: see memory reference.

---

## 13. References

### pybticino documentation

- **WebRTC Signaling Protocol**: https://github.com/k-the-hidden-hero/pybticino/blob/main/docs/webrtc-signaling.md
  Detailed protocol documentation for WebRTC signaling via Netatmo cloud, including offer/answer mode flows, message formats, and session lifecycle.

- **WebRTC Audio Mechanism**: https://github.com/k-the-hidden-hero/pybticino/blob/main/docs/webrtc-audio.md
  Analysis of the device mic activation behavior, RTP packet requirement, and the AudioContext oscillator solution.

- **Reverse Engineering Notes**: https://github.com/k-the-hidden-hero/pybticino/blob/main/docs/reverse-engineering-notes.md
  Raw findings from APK decompilation and traffic capture of the Home+Security app.

### Companion projects

- **bticino_ha_extras**: https://github.com/k-the-hidden-hero/bticino_ha_extras
  Custom Lovelace cards and automation blueprints for the BTicino integration, including the WebRTC audio card with microphone support.

### Home Assistant references

- **HA WebRTC Camera API**: `homeassistant/components/camera/__init__.py` and `webrtc.py`
- **HA audio muting**: PR #25767 (by design, built-in player mutes WebRTC audio)
- **Ring integration** (closest architectural match): `homeassistant/components/ring/`
- **Nest integration** (session management reference): `homeassistant/components/nest/`
