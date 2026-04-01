# BTicino Intercom v2.0 — WebRTC Architecture & Reference Guide

> **Purpose**: Comprehensive architectural reference for implementing WebRTC live video,
> event entity migration, and two-way audio in the BTicino Intercom integration.
> Written to be self-contained — a new session can pick this up without prior context.
>
> **Date**: 2026-04-01
> **Branch**: `dev/webrtc` (based on `main` v1.9.2)
> **Status**: WebRTC signaling validated end-to-end, implementation in progress

---

## Table of Contents

1. [Current State](#1-current-state)
2. [Industry Research — How Others Do It](#2-industry-research)
3. [Recommended Architecture (Ring+Nest+UniFi Mix)](#3-recommended-architecture)
4. [WebRTC Implementation Guide](#4-webrtc-implementation-guide)
5. [Event Entity Migration](#5-event-entity-migration)
6. [Two-Way Audio](#6-two-way-audio)
7. [SDP Answer Fixing](#7-sdp-answer-fixing)
8. [Session Management](#8-session-management)
9. [ICE / TURN / STUN](#9-ice-turn-stun)
10. [Code References](#10-code-references)
11. [Remaining Work Checklist](#11-remaining-work-checklist)

---

## 1. Current State

### What exists (v1.9.2 on `main`)

- **5 platforms**: lock, binary_sensor, sensor, light, camera (snapshot/vignette only)
- **WebSocket push** for real-time RTC events (call/rescind/terminate) + 5-min polling
- **Token persistence** via HA Store (avoids login emails on restart)
- **Reauth flow**, diagnostics endpoint, quality_scale.yaml
- **92 tests** passing

### What exists on `dev/webrtc` (WIP, uncommitted → committed as WIP)

- `SignalingClient` in pybticino — connects to `wss://app-ws.netatmo.net/appws/`
- Camera entity with `async_handle_async_webrtc_offer` skeleton
- Enhanced coordinator event processing
- **Signaling validated**: offer sent to Netatmo server, ack received, session established
- Device responded "Max number of peers reached" (not a bug — other sessions were active)

### Key discoveries from reverse engineering

- **Signaling WS URL**: `wss://app-ws.netatmo.net/appws/` (NOT `app.netatmo.net` — that redirects)
- **Ack format**: `{type: "ack", session_id, tag_id, correlation_id, status: "ok"}`
- **Terminate with error**: `{type: "rtc", data: {type: "terminate", error: {code: 1, message: "..."}}}`
- **TURN endpoint**: `/turn` returns 404 — correct path not yet found (not blocking for LAN)

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

## 2. Industry Research

Analyzed 5 major doorbell/intercom integrations in HA core to identify best patterns.

### 2.1 Ring (`homeassistant/components/ring/`)

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

### 2.2 Google Nest (`homeassistant/components/nest/`)

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

### 2.3 UniFi Protect (`homeassistant/components/unifiprotect/`)

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

### 2.4 Reolink (`homeassistant/components/reolink/`)

**Why it matters**: Multi-fallback event detection, entity description pattern.

| Aspect | Implementation |
|---|---|
| **Event detection** | 4-level fallback: TCP Baichuan → ONVIF webhook → ONVIF long poll → fast poll 5s |
| **Video** | RTSP/RTMP → go2rtc (no custom WebRTC) |
| **Two-way audio** | Not implemented in HA |
| **Doorbell entity** | `binary_sensor` only (no event entity) |
| **Entity pattern** | `ReolinkBinarySensorEntityDescription` dataclass with `supported` lambdas |

Less relevant for our architecture but notable for resilient push event handling.

### 2.5 Netatmo (`homeassistant/components/netatmo/`)

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

### 2.6 Summary Comparison Table

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

## 3. Recommended Architecture (Ring+Nest+UniFi Mix)

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

## 4. WebRTC Implementation Guide

### 4.1 Camera entity structure

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

### 4.2 Signaling flow (via pybticino SignalingClient)

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

### 4.3 What to implement in pybticino vs bticino_intercom

| Layer | Component | Responsibility |
|---|---|---|
| **pybticino** | `SignalingClient` | WebSocket connection to `appws/`, send offer, receive answer, send/receive ICE, send terminate |
| **pybticino** | `WebRtcStream` (new) | Dataclass holding session_id, answer_sdp, expires_at, media_session_id |
| **bticino_intercom** | `camera.py` | HA WebRTC protocol methods, session tracking dict, SDP fixing |
| **bticino_intercom** | `coordinator.py` | Active call state, dispatch SIGNAL_CALL_RECEIVED for event entity |

---

## 5. Event Entity Migration

### 5.1 Current state

Doorbell ring is a `binary_sensor` in `binary_sensor.py` that:
- Turns ON when an RTC `call` event is received via WebSocket
- Turns OFF after 30s timeout or when `terminate` is received
- Uses `SIGNAL_CALL_RECEIVED` dispatcher signal

### 5.2 Target state (following Ring's pattern)

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

### 5.3 Migration strategy (following UniFi's dual pattern)

1. **v2.0**: Add `event` entity, keep `binary_sensor` — both fire from same WS event
2. **v2.1**: Deprecate `binary_sensor` with HA repair issue (like Ring did in 2025.4)
3. **v2.2**: Remove `binary_sensor`

### 5.4 Event lifecycle improvement (from UniFi)

Current: 30s hardcoded timeout for binary_sensor auto-off.
Improved:
- **Primary reset**: `terminate` WebSocket event → immediate off
- **Safety net**: 30s timeout only if `terminate` is never received (network issue)
- **`rescind` event**: Also resets the sensor (call cancelled before answer)

---

## 6. Two-Way Audio

### 6.1 How Ring does it

Two-way audio is NOT a separate feature — it's part of the WebRTC stream:
- The `stream_options` include `audio_enabled: True`
- The browser's SDP offer includes an audio track with `sendrecv` direction
- Ring's SDP answer includes `sendrecv` for audio
- Result: bidirectional audio via standard WebRTC

### 6.2 How to implement for BTicino

1. Ensure the SDP offer from the browser includes audio `sendrecv`
   (HA frontend does this when the camera supports `CameraEntityFeature.STREAM`)
2. Pass the full offer (including audio) to Netatmo signaling
3. Netatmo's answer should include audio capabilities
4. If the answer has `sendrecv` for audio → two-way audio works automatically
5. If not → we may need to modify the SDP or it may be a device limitation

### 6.3 Fallback if native two-way audio doesn't work

If Netatmo's WebRTC doesn't support bidirectional audio:
- **Option A**: SDP manipulation — force `sendrecv` in the answer
- **Option B**: Separate audio channel (would need reverse engineering)
- **Option C**: Accept receive-only audio (user can see+hear but not talk)

Don't pre-optimize — try the simple path first (just pass the offer with audio).

---

## 7. SDP Answer Fixing

### 7.1 Why it's needed

Both Ring and Nest fix the SDP answer from the cloud. Common issues:

1. **Direction mismatch**: Cloud returns `sendrecv` for tracks where browser offered `recvonly`.
   RFC 3264 says the answer should be `sendonly` for `recvonly` offers.
2. **Codec ordering**: Cloud may reorder codecs in a way browsers don't like.
3. **Missing attributes**: Some required SDP attributes may be missing.

### 7.2 Reference implementations

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

### 7.3 Our implementation plan

1. First, capture raw SDP answers from Netatmo signaling (log them)
2. Test without fixing — may work as-is
3. If browser rejects the answer, implement `fix_sdp_answer()` based on observed issues
4. Keep it minimal — only fix what's actually broken

---

## 8. Session Management

### 8.1 Pattern (from Nest)

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

### 8.2 Session refresh (from Nest's StreamRefresh)

Netatmo WebRTC sessions likely have a timeout. Implement refresh:
- Track `expires_at` per session
- Schedule `async_call_later` to refresh before expiry
- On refresh failure: exponential backoff (1min, 1.5x, max 10min)
- On success: reschedule

---

## 9. ICE / TURN / STUN

### 9.1 Strategy: try vanilla ICE first (Nest pattern)

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

### 9.2 TURN server discovery

Current state: `/turn` endpoint returns 404.

Options to investigate:
1. Check APK strings more carefully for the correct TURN endpoint
2. Capture actual WebRTC traffic from the BTicino app to see what TURN servers it uses
3. Check if TURN candidates are bundled in Netatmo's SDP answer
4. If Netatmo provides TURN in the SDP → we don't need to discover the endpoint at all

### 9.3 Client configuration

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

## 10. Code References

### 10.1 HA Core WebRTC infrastructure

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

### 10.2 Reference integration code to study

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

### 10.3 Our codebase (current key files)

| File | Lines | Key content |
|---|---|---|
| `__init__.py` | ~370 | WS manager, token persistence, SignalingClient setup |
| `coordinator.py` | ~470 | `_process_websocket_event()`, `_handle_websocket_message()` |
| `camera.py` | ~460 | Snapshot/vignette + WebRTC WIP (on dev/webrtc) |
| `binary_sensor.py` | ~280 | Call sensor — will add event entity alongside |
| `const.py` | ~70 | `SIGNAL_CALL_RECEIVED`, module subtypes |

### 10.4 pybticino (our library)

| File | Key content |
|---|---|
| `auth.py` | `AuthHandler` with `token_callback` + `set_tokens()` (v1.6.0) |
| `websocket.py` | `WebsocketClient` — persistent WS for push events |
| `signaling.py` | `SignalingClient` — WS to `appws/` for WebRTC signaling (dev/webrtc) |
| `account.py` | `AsyncAccount` — REST API (topology, status, events) |

---

## 11. Remaining Work Checklist

### Phase 1: WebRTC Live Video (core)

- [ ] Test WebRTC offer with free device (no other active sessions)
- [ ] Capture and log raw SDP answer from Netatmo
- [ ] Implement `async_handle_async_webrtc_offer` fully in camera.py
- [ ] Determine ICE strategy (vanilla vs trickle) based on Netatmo's SDP answer
- [ ] Implement `close_webrtc_session` with terminate signaling
- [ ] Add session tracking dict
- [ ] Test end-to-end: browser → HA → Netatmo → video playing

### Phase 2: Robustness

- [ ] Implement SDP answer fixing if needed (based on Phase 1 observations)
- [ ] Add session refresh/keepalive mechanism
- [ ] Handle `WebRTCError` for error cases (max peers, device offline, etc.)
- [ ] Implement `_async_get_webrtc_client_configuration` with STUN/TURN if needed
- [ ] Find correct TURN endpoint (or confirm it's not needed in LAN)

### Phase 3: Event Entity

- [ ] Create `event.py` with `BticinoDoorbellEvent` (EventDeviceClass.DOORBELL)
- [ ] Wire to `SIGNAL_CALL_RECEIVED` dispatcher
- [ ] Add to PLATFORMS in const.py
- [ ] Keep binary_sensor for backwards compat (dual entity like UniFi)
- [ ] Use `terminate` WS event as primary reset for binary_sensor

### Phase 4: Two-Way Audio

- [ ] Test if Netatmo's SDP answer includes sendrecv for audio
- [ ] If yes: two-way audio works automatically via WebRTC
- [ ] If no: investigate SDP manipulation or alternative approaches

### Phase 5: Release

- [ ] Bump to v2.0.0
- [ ] Update README with WebRTC/live video documentation
- [ ] Test on production HA (dev tag first, then final)
- [ ] Update pybticino if signaling changes needed

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
