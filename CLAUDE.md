# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

A Home Assistant custom integration for BTicino Classe 100X/300X video intercom systems. Communicates with the BTicino/Netatmo cloud API via the [pybticino](https://github.com/k-the-hidden-hero/pybticino) library. Uses a persistent WebSocket connection for real-time call events and periodic polling (every 5 minutes) for state synchronization.

**Only works with Netatmo cloud-connected devices** (Home+Security app), not the legacy BTicino cloud.

## Development

There is no build system, test suite, or linter configured in this repository. The code runs as a Home Assistant custom component loaded from `custom_components/bticino_intercom/`.

To validate the integration structure locally, you can run the same checks CI uses:
- **HassFest** (HA manifest validation): `docker run --rm -v $(pwd):/github/workspace ghcr.io/home-assistant/hassfest`
- **HACS validation**: runs via `hacs/action@main` in CI

CI runs on every push/PR (`.github/workflows/hacs.yaml`). Releases are triggered by pushing a `v*` tag (`.github/workflows/release.yaml`).

## Architecture

All code lives in `custom_components/bticino_intercom/`. Key data flow:

1. **`config_flow.py`** - UI-based setup: authenticates via `pybticino.AuthHandler`, fetches home topology via `AsyncAccount`, lets user select a home. Supports `light_as_lock` option (uses lighting relay instead of dedicated lock module).

2. **`__init__.py`** - Entry setup: creates `AuthHandler` + `AsyncAccount` + `WebsocketClient` + `BticinoIntercomCoordinator`. Manages the WebSocket connection lifecycle with smart retry backoff (separate schedules for boot vs runtime). Registers shutdown handlers to cleanly cancel the WebSocket task.

3. **`coordinator.py`** (`BticinoIntercomCoordinator`) - Central data hub extending HA's `DataUpdateCoordinator`. Two data paths:
   - **Polling**: `_async_update_data()` fetches topology, home status, and event history from the API
   - **Push**: `_handle_websocket_message()` processes real-time RTC events (call/rescind/terminate), updates state immediately via `async_set_updated_data()`, then requests a full refresh for consistency

4. **Entity platforms** consume coordinator data:
   - `lock.py` - Door locks (BNDL modules), optimistic state with relock delay
   - `binary_sensor.py` - External unit call status (BNEU modules), auto-off after 30s timeout
   - `sensor.py` - Last call event sensor tied to the bridge module
   - `light.py` - Staircase lights (BNSL modules), auto-off after 10s
   - `camera.py` - Camera entity with image caching (5 min)

5. **`const.py`** - Module subtype constants (`SUBTYPE_DOORLOCK`, `SUBTYPE_EXTERNAL_UNIT`, `SUBTYPE_STAIRCASE_LIGHT`) and their platform mappings. Event types for logbook integration.

## Key Concepts

- **Module subtypes**: BTicino devices are identified by variant subtypes: `bndl_doorlock`, `bneu_external_unit`, `bnsl_staircase_light`. The bridge module is identified by having a MAC address format ID.
- **WebSocket events**: RTC events (`call`, `rescind`, `terminate`) are dispatched via HA's dispatcher system (`SIGNAL_CALL_RECEIVED`) and fired as logbook events.
- **Data storage**: `hass.data[DOMAIN][entry.entry_id]` holds the coordinator, websocket client, and websocket task references keyed by constants defined in `__init__.py`.

## WebRTC Live Video (v2.0)

The integration supports WebRTC live video streaming via the Netatmo cloud.

- **Camera entities**: One per BNEU (external unit) module. Each external unit in the BTicino topology gets its own camera entity with independent WebRTC streaming.
- **Signaling**: Uses pybticino's `SignalingClient`, which connects to `wss://app-ws.netatmo.net/appws/`. This is a separate WebSocket from the push event WebSocket (`/ws/`). The signaling client handles offer/answer exchange for WebRTC session establishment.
- **Two modes**:
  - **Offer mode** (on-demand): HA sends SDP offer to the device via Netatmo cloud, receives SDP answer back. Used for live viewing from the dashboard.
  - **Answer mode** (incoming call): Device sends SDP offer via push WebSocket when someone rings. HA can respond with an SDP answer to join the call. (Documented but not yet implemented in camera entity.)
- **Audio**: The device mic requires real RTP audio packets to activate (not just SDP `sendrecv` negotiation). A custom Lovelace card from [bticino_ha_extras](https://github.com/k-the-hidden-hero/bticino_ha_extras) is needed to send audio and unmute playback, since HA's built-in camera player mutes audio by design.
- **SDP manipulation**: `camera.py` contains SDP manipulation methods (direction forcing, codec reordering) that were explored during development. These are currently disabled because the audio issue is RTP-related, not SDP-related.
- **Session timeout**: ~30s in offer mode (device-side behavior, not configurable from the client).
- **Event entity**: `event.py` provides `EventDeviceClass.DOORBELL` alongside the existing `binary_sensor` for doorbell ring detection (dual entity pattern, following UniFi's approach).
- **Browser compatibility**: Chrome/Chromium only. Firefox is NOT supported due to a device firmware bug: the BTicino BNC1 uses hardcoded Chrome-compatible RTP payload type numbers (PT=111/Opus, PT=109/H264) regardless of SDP negotiation. Firefox uses different PTs and drops the mismatched packets. DTLS and SRTP work correctly in Firefox — the issue is exclusively at the RTP payload type layer. See `docs/firefox-webrtc-investigation.md` for the full analysis.

For protocol details, see the [pybticino WebRTC documentation](https://github.com/k-the-hidden-hero/pybticino/blob/main/docs/webrtc-signaling.md).

## Call History

The integration persists incoming call events (snapshots/vignettes) to disk so they remain viewable after the Azure SAS URLs expire (typically within minutes).

- **Storage**: `<config>/bticino_intercom/events/<entry_id>/<module_id>/` for images, `helpers.storage.Store` for metadata
- **Trigger**: `incoming_call` WebSocket push → `coordinator._process_status_event()` → `EventHistoryStore.async_record_call()` downloads images immediately
- **Closing**: RTC `terminate`/`rescind` → `coordinator._close_history_event()` marks the record as closed
- **Retention**: configurable via options flow (`history_enabled`, `history_retention_days`, `history_max_events`), defaults 30 days / 500 events
- **Browsing**: exposed via HA Media Source (`media-source://bticino_intercom/...`) and authenticated HTTP view (`/api/bticino_intercom/image/...`)
- **Files**: `history.py` (store), `media_source.py` (browse + HTTP view)

## Debug Service

When the integration logger is at DEBUG level, a `bticino_intercom.inject_test_event` service is registered. It injects a fake `incoming_call` push into the coordinator, triggering the full event pipeline (snapshot download, history recording, entity updates, logbook events).

Protected by HA authentication. Optional service_data parameters:
- `device_id`: module to attribute the event to (defaults to first available module)
- `session_id`: custom session ID (defaults to `test-<timestamp>`)
- `snapshot_url`: image URL to download as snapshot (defaults to picsum.photos)
- `vignette_url`: image URL to download as vignette (defaults to picsum.photos)

Activate with:
```yaml
logger:
  logs:
    custom_components.bticino_intercom: debug
```

## Version & Dependencies

Version is tracked in `manifest.json` (`version` field). The sole runtime dependency is `pybticino>=1.0`.
