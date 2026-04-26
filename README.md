# BTicino Classe 100X/300X Integration for Home Assistant

[![HACS Default](https://img.shields.io/badge/HACS-Default-41BDF5.svg)](https://github.com/hacs/integration)
[![GitHub Release](https://img.shields.io/github/v/release/k-the-hidden-hero/bticino_intercom)](https://github.com/k-the-hidden-hero/bticino_intercom/releases/latest)
[![CI](https://github.com/k-the-hidden-hero/bticino_intercom/actions/workflows/ci.yaml/badge.svg)](https://github.com/k-the-hidden-hero/bticino_intercom/actions/workflows/ci.yaml)
[![GitHub Issues](https://img.shields.io/github/issues/k-the-hidden-hero/bticino_intercom)](https://github.com/k-the-hidden-hero/bticino_intercom/issues)
[![License](https://img.shields.io/github/license/k-the-hidden-hero/bticino_intercom)](LICENSE)

A Home Assistant custom integration for **BTicino Classe 100X and 300X** video intercom systems. Monitor calls, control door locks, manage staircase lights, stream live video, and talk back — all from your Home Assistant dashboard.

Communicates with the BTicino/Netatmo cloud API via the [pybticino](https://github.com/k-the-hidden-hero/pybticino) library. Uses a persistent WebSocket connection for real-time call notifications and periodic polling for state synchronization.

> [!IMPORTANT]
> This integration works **only with Netatmo cloud-connected devices** (the ones managed via the **Home + Security** app).
>
> - Android: [Home + Security on Google Play](https://play.google.com/store/apps/details?id=com.netatmo.camera)
> - iOS: [Home + Security on App Store](https://apps.apple.com/us/app/home-security/id951725393)
>
> If your device uses the old "BTicino Door Entry" app, it is not compatible. BTicino has announced migration to the Netatmo platform — check with BTicino support for your device's status.

---

## Features

### v2.0 Highlights

- **WebRTC live video streaming** — On-demand live view from your intercom cameras, directly in the Home Assistant dashboard. Uses native WebRTC via the Netatmo cloud (no go2rtc or RTSP needed).
- **Doorbell event entity** — Modern `EventDeviceClass.DOORBELL` entity for doorbell ring detection, alongside the existing binary sensor for backwards compatibility.
- **Real-time snapshots** — Snapshot and vignette images from incoming call push events, available instantly when someone rings.
- **Multi-camera support** — One camera entity per external unit (BNEU module). Homes with multiple entrance cameras can view each independently.
- **Call history** — Incoming call snapshots and vignettes are saved locally as soon as the doorbell rings. Images remain viewable indefinitely, even after the cloud URLs expire. Browsable from the HA Media Browser. Configurable retention (default: 30 days, 500 events).
- **Per-module call tracking** — Call sessions tracked per module with retransmission deduplication, ensuring reliable event processing.
- **Two-way audio** — Hear the visitor and talk back via WebRTC. Requires the [bticino_ha_extras](https://github.com/k-the-hidden-hero/bticino_ha_extras) custom card (HA's built-in camera player mutes audio by design).

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
| **Light** | Staircase lights (BNSL modules). On/off control. |

### Real-time events

The integration fires logbook events for automations:

- `bticino_intercom_incoming_call` — someone rang the doorbell
- `bticino_intercom_answered_elsewhere` — call answered on another device
- `bticino_intercom_terminated` — call ended

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
> Home Assistant's built-in camera player mutes WebRTC audio by design. The custom card from `bticino_ha_extras` is required to hear the visitor and talk back through the intercom.

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
| "Something is blocking startup" | Other integrations (not this one since v1.8.1) | Unrelated to bticino_intercom |
| Delayed call notifications | WebSocket was disconnected | Check debug logs; the watchdog should reconnect within 10 minutes |
| Authentication failed | Wrong credentials or expired token | Re-enter credentials in the integration settings |
| No entities created | Modules not found in topology | Check that your intercom appears in the Home + Security app |

---

## v2.0 Beta Testing

v2.0 is currently in release candidate stage. If you want to help test, here's what you need.

### What to install

You need **two** custom components for the full v2.0 experience:

| Component | What it does | Install |
|---|---|---|
| **[bticino_intercom](https://github.com/k-the-hidden-hero/bticino_intercom)** | The integration (camera, sensors, locks, events, call history) | HACS or manual |
| **[bticino_ha_extras](https://github.com/k-the-hidden-hero/bticino_ha_extras)** | Custom Lovelace card with live video, two-way audio, call history UI | Manual (copy JS to `www/`) |

> [!IMPORTANT]
> **Do NOT use AlexxIT's WebRTC integration** (`custom_components/webrtc`) or other third-party WebRTC players to display the BTicino camera. They are not compatible with the BTicino signaling protocol and will cause crashes, especially on iOS/Safari (WebKit). Use the dedicated card from `bticino_ha_extras`.

### Installing the RC

**Integration (HACS):**
1. In HACS, go to **BTicino Intercom** > click the three-dot menu > **Redownload**
2. Enable **"Show beta versions"** and select the latest `2.0.0-rcX`
3. Restart Home Assistant

**Integration (manual):**
1. Download the latest `v2.0.0-rcX` from [Releases](https://github.com/k-the-hidden-hero/bticino_intercom/releases)
2. Replace `custom_components/bticino_intercom/` with the new version
3. Clear `__pycache__` if using file sync: `find custom_components/bticino_intercom -name __pycache__ -exec rm -rf {} +`
4. Restart Home Assistant

**Custom card:**
1. Download `bticino-intercom-card.js` from [bticino_ha_extras releases](https://github.com/k-the-hidden-hero/bticino_ha_extras/releases)
2. Copy to `config/www/bticino-intercom-card.js`
3. Add as a Dashboard resource: **Settings > Dashboards > Resources > Add Resource**
   - URL: `/local/bticino-intercom-card.js`
   - Type: **JavaScript Module**
4. Hard-refresh the browser (`Ctrl+Shift+R`) to clear cache

### Dashboard card example

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

When reporting a v2.0 bug:
1. Enable debug logging (see [Troubleshooting](#troubleshooting))
2. Reproduce the issue
3. Download the full log from **Settings > System > Logs > Download full log**
4. Open an [issue](https://github.com/k-the-hidden-hero/bticino_intercom/issues) with:
   - Your HA version, device model, integration version
   - The browser you're using
   - The full debug log attached (not pasted inline)

---

## Requirements

- Home Assistant 2026.3 or later
- Home Assistant 2025.x or later (Python 3.13+)
- A BTicino Classe 100X or 300X connected to the Netatmo cloud
- [pybticino](https://github.com/k-the-hidden-hero/pybticino) >= 1.7.1 (installed automatically)

---

## Contributing

Found a bug or want a feature? [Open an issue](https://github.com/k-the-hidden-hero/bticino_intercom/issues).

Pull requests are welcome. The project uses:
- **ruff** for linting and formatting
- **pytest** with `pytest-homeassistant-custom-component` for testing (159 tests)
- **bandit** for security scanning
- CI runs on every push and PR

---

## License

MIT License. See [LICENSE](LICENSE) for details.
