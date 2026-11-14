# BTicino Classe 100X/300X Integration for Home Assistant

[![HACS Default](https://img.shields.io/badge/HACS-Default-41BDF5.svg)](https://github.com/hacs/integration)
[![GitHub Release](https://img.shields.io/github/v/release/k-the-hidden-hero/bticino_intercom)](https://github.com/k-the-hidden-hero/bticino_intercom/releases/latest)
[![CI](https://github.com/k-the-hidden-hero/bticino_intercom/actions/workflows/ci.yaml/badge.svg)](https://github.com/k-the-hidden-hero/bticino_intercom/actions/workflows/ci.yaml)
[![GitHub Issues](https://img.shields.io/github/issues/k-the-hidden-hero/bticino_intercom)](https://github.com/k-the-hidden-hero/bticino_intercom/issues)
[![License](https://img.shields.io/github/license/k-the-hidden-hero/bticino_intercom)](LICENSE)

A Home Assistant custom integration for **BTicino Classe 100X, 300X and 300 EOS** video intercom systems, and for **BTicino/Legrand EasyKit** WiFi kits. Monitor calls, control door locks, manage staircase lights, stream live video, and talk back — all from your Home Assistant dashboard.

Communicates with the BTicino/Netatmo cloud API via the [pybticino](https://github.com/k-the-hidden-hero/pybticino) library. Uses a persistent WebSocket connection for real-time call notifications and periodic polling for state synchronization.

> [!IMPORTANT]
> This integration works **only with Netatmo cloud-connected devices** (the ones managed via the **Home + Security** app).
>
> - Android: [Home + Security on Google Play](https://play.google.com/store/apps/details?id=com.netatmo.camera)
> - iOS: [Home + Security on App Store](https://apps.apple.com/us/app/home-security/id951725393)
>
> If your device uses the old "BTicino Door Entry" app, it is not compatible. BTicino has announced migration to the Netatmo platform — check with BTicino support for your device's status.
>
> EasyKit WiFi kits (e.g. 310913, 360910) are supported since v2.0.4. On EasyKit's `BDIY`
> bridge an incoming call's offer arrives only over FCM, so answering a ring from Home
> Assistant is not supported yet. The work is under way in [#72](https://github.com/k-the-hidden-hero/bticino_intercom/issues/72).

---

## Features

### Highlights

- **WebRTC live video streaming** — On-demand live view from your intercom cameras, directly in the Home Assistant dashboard. Uses native WebRTC via the Netatmo cloud (no go2rtc or RTSP needed).
- **Doorbell event entity** — Modern `EventDeviceClass.DOORBELL` entity for doorbell ring detection, alongside the existing binary sensor for backwards compatibility.
- **Real-time snapshots** — Snapshot and vignette images from incoming call push events, available instantly when someone rings.
- **Multi-camera support** — One camera entity per external unit (BNEU module). Homes with multiple entrance cameras can view each independently.
- **Call history** — Incoming call snapshots and vignettes are saved locally as soon as the doorbell rings. Images remain viewable indefinitely, even after the cloud URLs expire. Browsable from the HA Media Browser. Configurable retention (default: 30 days, 500 events).
- **Per-module call tracking** — Call sessions tracked per module with retransmission deduplication, ensuring reliable event processing.
- **Two-way audio** — Hear the visitor and talk back via WebRTC. On Home Assistant 2026.11 and later the camera's own view has a talk button (see [Native two-way audio](#native-two-way-audio-home-assistant-202611)); on older versions it needs the [bticino_ha_extras](https://github.com/k-the-hidden-hero/bticino_ha_extras) custom card.

### Entities

| Entity type | What it does |
|---|---|
| **Camera** | WebRTC live video streaming and event snapshots. One entity per external unit. |
| **Event** | Doorbell ring detection (`EventDeviceClass.DOORBELL`). Fires on incoming calls. |
| **Lock** | Control door locks (BNDL modules). Open/close with optimistic state and automatic relock timer. |
| **Binary Sensor** | Real-time incoming call detection (BNEU external units). Auto-off after 30 seconds. |
| **Sensor — Last Event** | Shows the last call event type (incoming, answered elsewhere, terminated) with timestamp and snapshot URLs. |
| **Sensor — Last Call Timestamp** | Timestamp sensor for the most recent completed call. |
| **Sensor — Bridge Diagnostics** | Uptime, WiFi strength, WebSocket status, local IP, last seen, last config update. |
| **Light** | Staircase lights (BNSL modules, or BNVL on the 344742). On/off control. |
| **Select — Do Not Disturb** | Classe 300X (BNC3) only. Ringtone mode, as in the Home + Security app: ringtone on, temporarily muted, always muted. |
| **Number — Do Not Disturb Duration** | Classe 300X (BNC3) only. How many minutes "temporarily muted" lasts (default 60). |
| **Switch / Number — Professional Studio** | Classe 300X (BNC3) only, when Professional Studio is configured on the device. |

Entities also carry a `module_id` attribute. The camera and the doorbell of the same external unit share it, so a card can tell which camera belongs to which doorbell when there are several entrances.

### Real-time events

The integration fires these events on the Home Assistant bus, for automations and the logbook:

- `bticino_intercom_incoming_call`: someone rang the doorbell
- `bticino_intercom_answered_elsewhere`: the call was answered on another device
- `bticino_intercom_missed_call`: nobody answered
- `bticino_intercom_terminated`: the call ended
- `bticino_intercom_call`: one event when a call rings (`type: ring`, with the snapshot and the camera entity) and one when it ends (`type: end`, with a `reason`)

The `bticino_intercom.reject_call` action ends the ringing call on every device.

### Automation Blueprints

Ready-to-use blueprints for common intercom automations. Click a button to import directly into your Home Assistant:

| Blueprint | Description | Import |
|---|---|---|
| **Actionable Doorbell Notification** | Snapshot preview + up to 3 unlock buttons. Rings even on silent (alarm stream). | [![Import](https://my.home-assistant.io/badges/blueprint_import.svg)](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fgithub.com%2Fk-the-hidden-hero%2Fbticino_intercom%2Fblob%2Fmain%2Fblueprints%2Fautomation%2Fdoorbell_actionable_notification.yaml) |
| **Doorbell Notification** | Mobile notification with snapshot and "Open Door" / "Dismiss" buttons | [![Import](https://my.home-assistant.io/badges/blueprint_import.svg)](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fgithub.com%2Fk-the-hidden-hero%2Fbticino_intercom%2Fblob%2Fmain%2Fblueprints%2Fautomation%2Fdoorbell_notification.yaml) |
| **Auto-Open on Schedule** | Automatically open the door during a time window (e.g., for deliveries) | [![Import](https://my.home-assistant.io/badges/blueprint_import.svg)](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fgithub.com%2Fk-the-hidden-hero%2Fbticino_intercom%2Fblob%2Fmain%2Fblueprints%2Fautomation%2Fdoorbell_auto_open.yaml) |
| **Snapshot to Service** | Save snapshot to file and fire an event for face recognition or logging | [![Import](https://my.home-assistant.io/badges/blueprint_import.svg)](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fgithub.com%2Fk-the-hidden-hero%2Fbticino_intercom%2Fblob%2Fmain%2Fblueprints%2Fautomation%2Fdoorbell_snapshot_to_service.yaml) |
| **Missed Call Log** | Get notified when a doorbell call goes unanswered | [![Import](https://my.home-assistant.io/badges/blueprint_import.svg)](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fgithub.com%2Fk-the-hidden-hero%2Fbticino_intercom%2Fblob%2Fmain%2Fblueprints%2Fautomation%2Fmissed_call_log.yaml) |

All blueprints are in the [`blueprints/automation/`](blueprints/automation/) folder. You can also install them manually by copying the YAML file to your `config/blueprints/automation/bticino_intercom/` directory.

### Companion: bticino_ha_extras

For the full experience, check out [bticino_ha_extras](https://github.com/k-the-hidden-hero/bticino_ha_extras) — a companion repository with:

- **Custom Lovelace card** for WebRTC video with audio support (unmuted playback + microphone talk-back)
- Additional automation blueprints

> [!NOTE]
> Before Home Assistant 2026.11 the built-in camera player has no way to talk back, so the custom card from `bticino_ha_extras` is required to hear the visitor and talk back through the intercom. From 2026.11 the built-in player can do it too: see below.

### Native two-way audio (Home Assistant 2026.11)

Home Assistant 2026.11 added two-way audio to its own camera player, as a Labs preview. The external unit cameras and **Call Home** declare it, so no custom card is needed to talk back:

1. Go to **Settings** → **System** → **Labs** and turn on **Two-way audio**.
2. Open Home Assistant over **HTTPS** (Nabu Casa, or your own certificate). Browsers only give the microphone to secure pages.
3. Open the camera (for example from its more-info dialog) and press the microphone button.

Things to know:

- Pressing the microphone restarts the stream: the player closes the running session and opens a new one that carries your voice. The video drops for a moment.
- If the doorbell is ringing and the intercom sent its call video, the first stream answers that call. Pressing the microphone then ends that call and opens a live view of the same unit, with the microphone on.
- The browser limits below still apply: the intercom only works with Chromium-based browsers.

### Light as Lock mode

Some BTicino installations use the lighting relay instead of a dedicated lock module. Enable **"Use lighting relay as lock"** in the integration options to expose the light module as a lock entity.

---

## Installation

### HACS (recommended)

> **Prerequisites:** You need [HACS](https://hacs.xyz/) installed in your Home Assistant. If you don't have it yet, follow the [HACS installation guide](https://hacs.xyz/docs/use/download/download/) first.

**Step 1 — Add the repository to HACS:**

[![Add Repository](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=k-the-hidden-hero&repository=bticino_intercom&category=integration)

Click the button above to add the repository. This opens HACS in your Home Assistant and adds the BTicino Intercom repository. If the button doesn't work, add it manually:

1. Open **HACS** > **Integrations** > click the **three-dot menu** (top right) > **Custom repositories**
2. Paste `https://github.com/k-the-hidden-hero/bticino_intercom` as the URL
3. Select **Integration** as the category and click **Add**

**Step 2 — Download the integration:**

1. In HACS, find **BTicino Intercom** in the integration list (search if needed)
2. Click on it, then click **Download** (bottom right)
3. Select the latest version and confirm
4. **Restart Home Assistant**

**Step 3 — Add the integration to Home Assistant:**

[![Add Integration](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=bticino_intercom)

Click the button above to start the setup, or go to **Settings > Devices & Services > + Add Integration** and search for **BTicino Intercom**.

### Manual

1. Download the [latest release](https://github.com/k-the-hidden-hero/bticino_intercom/releases/latest) zip file
2. Extract the `custom_components/bticino_intercom` folder into your Home Assistant `config/custom_components/` directory
3. Restart Home Assistant

---

## Setup

During setup you will be asked to enter your Netatmo account **email** and **password**. If you have multiple homes, you'll select the one with your intercom. You can also enable "Light as Lock" if your setup uses the lighting relay for the door.

> [!TIP]
> **We strongly recommend creating a dedicated Netatmo account** for Home Assistant instead of using your personal account. See the section below for why and how.

---

## Recommended: Use a Dedicated Account

The BTicino/Netatmo cloud API has limitations on concurrent sessions. If you use the same account on both the Home + Security app and Home Assistant, you may experience:

- WebSocket disconnections
- Delayed notifications
- Intermittent API errors

### How to create a dedicated account (5 minutes)

Gmail (and Google Workspace) supports **address aliases** using the `+` character. This lets you create a new Netatmo account without needing a separate email address.

For example, if your email is `yourname@gmail.com`, you can use:

```
yourname+homeassistant@gmail.com
```

All emails sent to this address will arrive in your existing `yourname@gmail.com` inbox. No setup needed — it works automatically. ([Learn more about Gmail aliases](https://support.google.com/mail/answer/22370))

**Step by step:**

1. **Create a new Netatmo account** at [my.netatmo.com](https://my.netatmo.com) using your `+homeassistant` alias email
2. **Set a strong password** for this account
3. **Open the Home + Security app** on your phone (logged in with your main account)
4. Go to your **Home settings** and **invite** the new alias email as a member
5. **Accept the invitation** by logging into the Home + Security app with the new account (you can use a second device, or log out and back in)
6. **Use the new account credentials** when setting up the BTicino Intercom integration in Home Assistant

Now your phone app and Home Assistant use separate accounts, each with their own session, and both have full access to the same home.

---

## How it works

### Architecture

```
BTicino Intercom <---> Netatmo Cloud <---> Home Assistant
                          |
                    +-----------+
                    | REST API  |  Polling every 5 min (topology, status, events)
                    +-----------+
                    | WebSocket |  Real-time push (call events, state changes)
                    +-----------+
                    | Signaling |  WebRTC offer/answer exchange (live video)
                    +-----------+
                    |  WebRTC   |  Direct media (video + audio) via STUN/TURN
                    +-----------+
```

### WebSocket connection

The integration maintains a persistent WebSocket connection to the Netatmo push server (`wss://app-ws.netatmo.net/ws/`). This is the same mechanism used by the official Android/iOS app to receive real-time notifications.

When someone rings your doorbell, the event is delivered via WebSocket in under a second. The integration processes it and immediately updates the binary sensor, fires a logbook event, and dispatches a signal for automations.

As a fallback, the integration also polls the API every 5 minutes. If the WebSocket connection drops, it automatically reconnects with an exponential backoff strategy.

To keep the connection alive, the integration **re-subscribes every hour** with a refreshed OAuth token on the existing connection — the same approach used by the official Android app. This prevents disconnections from token expiry.

A **watchdog** monitors WebSocket health: if no messages are received for 10 minutes, the connection is flagged as stale and forcefully reconnected. During temporary cloud outages, entities remain available with their last known state instead of going unavailable.

### Data flow

1. **Startup**: authenticates via OAuth2, fetches home topology, creates entities
2. **Polling** (every 5 min): refreshes module status, event history, bridge diagnostics
3. **WebSocket push**: real-time call events (ring, answer, terminate) update entities immediately
4. **WebRTC signaling**: on-demand live video via a separate signaling WebSocket (`appws/`)
5. **Actions**: lock/unlock and light on/off send commands via the REST API

---

## Troubleshooting

### Enable debug logging

Add to your `configuration.yaml`:

```yaml
logger:
  logs:
    custom_components.bticino_intercom: debug
    pybticino: debug
```

### Debug service: inject test events

When debug logging is active, the integration registers a `bticino_intercom.inject_test_event` service. This injects a fake incoming call into the system, triggering the full event pipeline — useful for testing automations, notifications, and call history without ringing the physical doorbell.

Call it from **Developer Tools > Services**:

```yaml
service: bticino_intercom.inject_test_event
data:
  device_id: "00:03:50:xx:xx:xx"   # optional, defaults to first module
  snapshot_url: "https://picsum.photos/640/480"  # optional
  vignette_url: "https://picsum.photos/160/120"  # optional
```

> [!NOTE]
> This service is only available when `custom_components.bticino_intercom` is set to `debug` in the logger configuration. It is protected by Home Assistant authentication.

### Common issues

| Problem | Cause | Solution |
|---|---|---|
| WebSocket errors at startup | Normal during first connection attempts | Wait 1-2 minutes, the integration retries automatically |
| Delayed call notifications | WebSocket was disconnected | Check debug logs; the watchdog should reconnect within 10 minutes |
| Authentication failed | Wrong credentials or expired token | Re-enter credentials in the integration settings |
| No entities created | Modules not found in topology | Check that your intercom appears in the Home + Security app |

---

## Live video and the companion card

| Component | What it does | Install |
|---|---|---|
| **[bticino_intercom](https://github.com/k-the-hidden-hero/bticino_intercom)** | The integration (camera, sensors, locks, events, call history) | HACS or manual |
| **[bticino_ha_extras](https://github.com/k-the-hidden-hero/bticino_ha_extras)** | Custom Lovelace card with live video, two-way audio and the call history | HACS (Frontend) or manual |

On Home Assistant 2026.11 and later the built-in camera player can talk back too (see [Native two-way audio](#native-two-way-audio-home-assistant-202611)). The card is still the way to get the call history and the door buttons in one place.

> [!IMPORTANT]
> **Do NOT use AlexxIT's WebRTC integration** (`custom_components/webrtc`) or other third-party WebRTC players to display the BTicino camera. They are not compatible with the BTicino signaling protocol and will cause crashes, especially on iOS/Safari (WebKit). Use the dedicated card from `bticino_ha_extras`.

### Installing the card

Add `https://github.com/k-the-hidden-hero/bticino_ha_extras` to HACS as a custom repository of type **Dashboard** (Frontend), download **BTicino Intercom Card**, and hard-refresh the browser. HACS registers the card resource by itself. The [bticino_ha_extras README](https://github.com/k-the-hidden-hero/bticino_ha_extras#readme) has the manual install and every option.

### Dashboard card example

The matching companion card can restore incoming-call controls when opened or
reconnected during the 30-second ringing window. It subscribes to live events,
then queries `bticino_intercom/get_active_calls` with its configured camera
`entity_ids`. The authenticated response contains public ring-event fields and
an `expires_at` Unix timestamp; camera read permissions apply. Finished or expired
calls are not returned, and querying does not start a call. Update both the
integration and card, restart Home Assistant, and hard-refresh the browser.

```yaml
type: custom:bticino-intercom-card
intercoms:
  - name: Citofono
    camera: camera.bticino_intercom_front_door
    actions:
      - entity: lock.front_gate
        service: lock.unlock
```

For multiple intercoms:

```yaml
type: custom:bticino-intercom-card
intercoms:
  - name: Ingresso principale
    camera: camera.bticino_intercom_front_door
    actions:
      - entity: lock.main_gate
        service: lock.unlock
      - entity: lock.building_door
        service: lock.unlock
  - name: Ingresso laterale
    camera: camera.bticino_intercom_side_entrance
    actions:
      - entity: lock.side_gate
        service: lock.unlock
```

### Browser compatibility

| Browser | Status | Notes |
|---|---|---|
| Chrome / Chromium (Desktop) | Supported | Full video + audio |
| Chrome (Android) | Supported | Full video + audio |
| Edge (Chromium) | Supported | Full video + audio |
| Safari / iOS (any browser) | Not supported | All iOS browsers use WebKit. The BTicino device firmware uses Chrome-specific RTP payload types — see [Firefox investigation](docs/firefox-webrtc-investigation.md) |
| Firefox | Not supported | Device firmware bug: hardcoded Chrome RTP payload types (PT=111/Opus, PT=109/H264) |
| HA Companion (Android) | Supported | Uses system WebView (Chromium) |
| HA Companion (iOS) | Not supported | Uses WebKit (same limitation as Safari) |

> [!NOTE]
> The browser limitation is caused by the BTicino device firmware (BNC1), which hardcodes Chrome/libwebrtc RTP payload type numbers in its media engine regardless of SDP negotiation. This cannot be fixed from the integration side. The official Netatmo app works on iOS because it bundles the same `libwebrtc` engine as Chrome, not because it does any special handling.

### Reporting issues

When reporting a bug:
1. Enable debug logging (see [Troubleshooting](#troubleshooting))
2. Reproduce the issue
3. Download the full log from **Settings > System > Logs > Download full log**
4. Open an [issue](https://github.com/k-the-hidden-hero/bticino_intercom/issues) with:
   - Your HA version, device model, integration version
   - The browser you're using
   - The full debug log attached (not pasted inline)

---

## Requirements

- Home Assistant 2026.5.0 or later (on older versions, use the v1.9.x releases)
- A BTicino Classe 100X, 300X or 300 EOS, or a BTicino/Legrand EasyKit, connected to the Netatmo cloud
- [pybticino](https://github.com/k-the-hidden-hero/pybticino) >= 1.10.0 (installed automatically)

---

## Contributing

Found a bug or want a feature? [Open an issue](https://github.com/k-the-hidden-hero/bticino_intercom/issues).

Pull requests are welcome. The project uses:
- **ruff** for linting and formatting (the version CI pins)
- **pytest** with `pytest-homeassistant-custom-component` for testing
- **bandit** for security scanning
- CI runs on every push and PR

Code that speaks the Netatmo/BTicino protocol belongs in [pybticino](https://github.com/k-the-hidden-hero/pybticino) rather than here; see [pybticino#31](https://github.com/k-the-hidden-hero/pybticino/issues/31). [CLAUDE.md](CLAUDE.md) has the commands and the device quirks worth knowing before you start.

---

## License

MIT License. See [LICENSE](LICENSE) for details.
