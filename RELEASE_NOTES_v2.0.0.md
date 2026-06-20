# v2.0.0 — WebRTC live video & two-way audio

After 12 release candidates and over two months of beta, v2.0 is stable.

## What's new since v1.9.x

### 🎥 WebRTC live video (multi-camera)
One camera entity per external unit (BNEU). Live video on demand from the
HA dashboard, with two-way audio when paired with the custom Lovelace card
from [bticino_ha_extras]. Chrome/Chromium only — see [Firefox investigation].

### 📞 Answer mode
Pick up an incoming call straight from HA: the device's SDP offer (sent
via push WebSocket) is answered with an SDP from the browser.

### 🗂️ Call history with persistent snapshots
Snapshots and vignettes are downloaded to disk the moment the call arrives,
so they remain viewable after the Azure SAS URLs expire. Browse via HA
Media Source or a dedicated authenticated HTTP view. Configurable retention
(default 30 days / 500 events).

### 🔔 Doorbell event entity (HA standard)
Adds `event.<intercom>_doorbell` using `DoorbellEventType.RING` — works
with HA's generic `doorbell.rang` trigger. The existing `binary_sensor`
stays (dual-entity pattern, same approach UniFi uses).

### 🏠 Call Home camera
Voice-only intercoms (no camera) get a "Call Home" camera entity with
a SEELE-style poster so the answer-mode UI still works.

### 📦 Blueprints
- `actionable_doorbell_notification` — Answer / Open / Reject buttons
- `incoming_call_notification` — with ringtone

### 🛡️ Stability
- 429 rate-limit prevention: terminate/rescind RTC retransmits are now
  deduplicated by session_id (fix for #56)
- Lock `open` no longer returns HTTP 500: lock entities that advertise the
  OPEN feature now implement `async_open` (fix for #57)
- Proactive WebSocket re-subscribe + resilient coordinator
- Smart retry backoff at boot

## Upgrade requirements
- Home Assistant ≥ **2026.5.0**
- pybticino `>=1.8.2`
- For two-way audio: [bticino_ha_extras] custom card (Chrome only)

For HA < 2026.5: stay on v1.9.x.

## Breaking changes
None for users on v1.9.x with default config. The event entity is new
(additive). DoorbellEventType is wire-compatible (still emits "ring").

## Thanks to the beta testers

This release exists because these people ran 12 RCs against real
intercoms in their homes and filed actionable bug reports:

- **@marcob79** — the most prolific reporter (#41, #46, #50, #51, #56);
  caught the Android Companion WebRTC quirk and the 429 cascade that
  ships fixed here
- **@hamanolo** — found the iOS Sound Only crash (#48)
- **@tamet83** — blueprint UX feedback and the silent-urgent notification
  spec (#52, #53)
- **@Doudy** — Home + Security user management edge cases (#49)
- **@filou01** — Classe 300X bridge detection (#54)

Grazie a tutti — sul serio.

## Open issues
- ⚠️ #50 — HA Companion Android WebRTC still under investigation
- 🔓 #57 — door open from HA on some installs: the HTTP 500 crash is fixed,
  but physically opening the entrance is setup-specific and still under
  investigation
- 🆕 #55 — BNC3 DND / Professional Studio controls (queued)

## What's next
- v1.9.x stable line continues for users on HA < 2026.5
- Reverse call (call the intercom from HA) is on the roadmap

[bticino_ha_extras]: https://github.com/k-the-hidden-hero/bticino_ha_extras
[Firefox investigation]: ./docs/firefox-webrtc-investigation.md
