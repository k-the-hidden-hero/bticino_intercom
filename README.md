# BTicino Classe 100X/300X Integration for Home Assistant

[![HACS Default](https://img.shields.io/badge/HACS-Default-41BDF5.svg)](https://github.com/hacs/integration)
[![GitHub Release](https://img.shields.io/github/v/release/k-the-hidden-hero/bticino_intercom)](https://github.com/k-the-hidden-hero/bticino_intercom/releases/latest)
[![CI](https://github.com/k-the-hidden-hero/bticino_intercom/actions/workflows/ci.yaml/badge.svg)](https://github.com/k-the-hidden-hero/bticino_intercom/actions/workflows/ci.yaml)
[![GitHub Issues](https://img.shields.io/github/issues/k-the-hidden-hero/bticino_intercom)](https://github.com/k-the-hidden-hero/bticino_intercom/issues)
[![License](https://img.shields.io/github/license/k-the-hidden-hero/bticino_intercom)](LICENSE)

A Home Assistant custom integration for **BTicino Classe 100X and 300X** video intercom systems. Monitor calls, control door locks, manage staircase lights, and view event snapshots — all from your Home Assistant dashboard.

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

### Entities

| Entity type | What it does |
|---|---|
| **Lock** | Control door locks (BNDL modules). Open/close with optimistic state and automatic relock timer. |
| **Binary Sensor** | Real-time incoming call detection (BNEU external units). Auto-off after 30 seconds. |
| **Sensor — Last Event** | Shows the last call event type (incoming, answered elsewhere, terminated) with timestamp and snapshot URLs. |
| **Sensor — Last Call Timestamp** | Timestamp sensor for the most recent completed call. |
| **Sensor — Bridge Diagnostics** | Uptime, WiFi strength, WebSocket status, local IP, last seen, last config update. |
| **Light** | Staircase lights (BNSL modules). On/off control. |
| **Camera** | Last event snapshot and vignette images, fetched from the Netatmo cloud. |

### Real-time events

The integration fires logbook events for automations:

- `bticino_intercom_incoming_call` — someone rang the doorbell
- `bticino_intercom_answered_elsewhere` — call answered on another device
- `bticino_intercom_terminated` — call ended

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

**Step 2 — Install the integration:**

1. In HACS, find **BTicino Intercom** in the integration list (search if needed)
2. Click on it, then click **Download** (bottom right)
3. Select the latest version and confirm
4. **Restart Home Assistant**

### Manual

1. Download the [latest release](https://github.com/k-the-hidden-hero/bticino_intercom/releases/latest) zip file
2. Extract the `custom_components/bticino_intercom` folder into your Home Assistant `config/custom_components/` directory
3. Restart Home Assistant

---

## Setup

1. Go to **Settings > Devices & Services**
2. Click **+ Add Integration** and search for **BTicino Intercom**
3. Enter your Netatmo account **email** and **password**
4. If you have multiple homes, select the one with your intercom
5. Optionally enable "Light as Lock" if your setup uses the lighting relay for the door

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
```

### WebSocket connection

The integration maintains a persistent WebSocket connection to the Netatmo push server (`wss://app-ws.netatmo.net/ws/`). This is the same mechanism used by the official Android/iOS app to receive real-time notifications.

When someone rings your doorbell, the event is delivered via WebSocket in under a second. The integration processes it and immediately updates the binary sensor, fires a logbook event, and dispatches a signal for automations.

As a fallback, the integration also polls the API every 5 minutes. If the WebSocket connection drops, it automatically reconnects with an exponential backoff strategy.

A **watchdog** monitors WebSocket health: if no messages are received for 10 minutes, the connection is flagged as stale and forcefully reconnected.

### Data flow

1. **Startup**: authenticates via OAuth2, fetches home topology, creates entities
2. **Polling** (every 5 min): refreshes module status, event history, bridge diagnostics
3. **WebSocket push**: real-time call events (ring, answer, terminate) update entities immediately
4. **Actions**: lock/unlock and light on/off send commands via the REST API

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

### Common issues

| Problem | Cause | Solution |
|---|---|---|
| WebSocket errors at startup | Normal during first connection attempts | Wait 1-2 minutes, the integration retries automatically |
| "Something is blocking startup" | Other integrations (not this one since v1.8.1) | Unrelated to bticino_intercom |
| Delayed call notifications | WebSocket was disconnected | Check debug logs; the watchdog should reconnect within 10 minutes |
| Authentication failed | Wrong credentials or expired token | Re-enter credentials in the integration settings |
| No entities created | Modules not found in topology | Check that your intercom appears in the Home + Security app |

---

## Requirements

- Home Assistant 2026.3 or later
- Python 3.14 or later (included in HA 2026.3+)
- A BTicino Classe 100X or 300X connected to the Netatmo cloud
- [pybticino](https://github.com/k-the-hidden-hero/pybticino) >= 1.5.3 (installed automatically)

---

## Contributing

Found a bug or want a feature? [Open an issue](https://github.com/k-the-hidden-hero/bticino_intercom/issues).

Pull requests are welcome. The project uses:
- **ruff** for linting and formatting
- **pytest** with `pytest-homeassistant-custom-component` for testing (87 tests)
- **bandit** for security scanning
- CI runs on every push and PR

---

## License

MIT License. See [LICENSE](LICENSE) for details.
