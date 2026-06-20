# Card Idle Redesign — Compact States with Multi-Intercom

## Overview

Redesign the bticino-intercom-card from a fixed-height video area (always showing a large dark rectangle in idle) to a compact card that expands only when needed. The card has three visual states: IDLE (compact), RING (expanded with snapshot), and LIVE (expanded with video).

The current card (`bticino-intercom-card.js` in bticino_ha_extras) is the working baseline. Changes must be incremental — each step must leave the card functional.

## Card source

`/home/koma/src/bticino_ha_extras/cards/bticino-intercom-card.js`

## States

### IDLE (compact, ~120px height)

The default resting state. Minimal dashboard footprint.

**Layout (top to bottom):**
1. **Tab bar**: one tab per configured intercom (e.g. "Strada", "Ingresso", "Garage"). Active tab has green underline. Swipeable.
2. **Content row**: intercom icon (circle) + name + subtitle "Ultima chiamata: HH:MM" + pill-shaped "Chiama" button (green).
3. **Action bar**: configurable quick actions (e.g. Apri / Luce scale / Cronologia). Same actions as current card config.
4. **Swipe dots**: pagination indicators matching number of intercoms.

**Single intercom**: tab bar and swipe dots are hidden. Just the content row + action bar.

**Interactions:**
- Tap "Chiama" → transition to LIVE (via CONNECTING)
- Tap action → execute service call (same as current)
- Swipe horizontally → switch intercom tab
- Tap tab → switch intercom

### RING (expanded with snapshot)

Triggered by `bticino_intercom_call` event with `type: ring`. The card auto-expands to show who's at the door.

**Layout (top to bottom):**
1. **Tab bar**: ringing intercom's tab turns orange with 🔔 prefix. Auto-switches to that tab.
2. **Snapshot area (4:3)**: vignette/snapshot image from the push event, `object-fit: cover`. Gradient overlay at top for label readability.
3. **Top overlay on snapshot**: "Qualcuno alla porta" label (orange) + "Sta suonando..." subtitle + animated "● RING" badge.
4. **Action bar**: Rispondi (green) / Apri (blue) / Rifiuta (red) — replaces normal actions during ring.
5. **Swipe dots**.

**Visual effects:**
- Card border turns orange with subtle glow (`box-shadow`)
- "● RING" badge blinks via CSS animation

**Audio:**
- `doorbell.wav` plays in loop via browser `Audio` API on ring start
- Stops on: user taps Rispondi/Apri/Rifiuta, or call ends (rescind/terminate event)

**Auto-answer flow (from mobile notification):**
- URL contains `?answer=<camera_entity_id>`
- Card skips RING state entirely → goes straight to LIVE with answer mode
- URL param is consumed and cleaned via `history.replaceState`

**Interactions:**
- Tap "Rispondi" → transition to LIVE (WebRTC answer mode)
- Tap "Apri" → call lock.unlock service + stay in RING (or transition to LIVE if configured)
- Tap "Rifiuta" → call reject_call service → transition to IDLE

### LIVE (expanded with video)

Active WebRTC streaming session.

**Layout (top to bottom):**
1. **Tab bar**: active tab turns red with "● LIVE" indicator.
2. **Video area (4:3)**: live WebRTC stream, `object-fit: contain`. Same area as snapshot in RING state — crossfade transition.
3. **Video controls overlay**: floating over video bottom — Hangup (red) / Mute / Mic / Fullscreen. Semi-transparent background. Auto-hide after inactivity, tap video to show.
4. **Action bar**: normal intercom actions return — Apri / Luce scale / Cronologia. Always visible below video during call for quick door opening.
5. **Swipe dots**.

**Interactions:**
- Tap Hangup → close WebRTC → transition to IDLE
- Tap Apri/Luce → execute service call while call continues
- Call ends remotely → transition to IDLE with missed call banner (5s auto-dismiss)

## Transitions

| From | To | Trigger | Animation |
|------|-----|---------|-----------|
| IDLE | RING | `bticino_intercom_call` ring event | Height expands (CSS transition), snapshot fades in |
| IDLE | CONNECTING→LIVE | User taps "Chiama" | Height expands, connecting overlay with pulse rings |
| RING | LIVE | User taps "Rispondi" | Crossfade snapshot→video (same 4:3 area), no height change |
| RING | IDLE | User taps "Rifiuta" or call ends | Height collapses, border glow fades |
| LIVE | IDLE | Hangup or remote end | Height collapses. If remote end while ring overlay was showing: missed call banner 5s |
| Any | RING | New ring on different intercom | Tab auto-switches, snapshot appears |

All height transitions use CSS `transition` on a wrapper element. The video area and snapshot share the same container — the content changes, not the container.

## Multi-Intercom

- Config uses `intercoms[]` array (Phase 2 config format from project roadmap)
- Each intercom has: `name`, `camera` (entity), `actions[]`
- Tab bar renders one tab per intercom
- Swipe gesture on content area changes active intercom
- Swipe dots at bottom indicate current position
- Each intercom maintains independent state (one can be LIVE while others are IDLE)
- Ring event auto-switches to ringing intercom's tab

**Single intercom fallback:** when only one intercom is configured (or legacy flat config), tab bar and swipe dots are hidden entirely. The card behaves identically otherwise.

## Integration Dependencies

No integration-side changes needed. This redesign is purely card-side (bticino_ha_extras). It consumes:

- `bticino_intercom_call` events (already implemented)
- `?answer=` URL param handling (already implemented)
- `doorbell.wav` static path (already registered)
- WebRTC signaling via HA camera entity (already implemented)
- `media_source/browse_media` for call history (already implemented)

## Incremental Implementation Strategy

The current card works. Each step must leave a functional card.

1. **Step 1**: Refactor IDLE state to compact layout (tab bar + content row + action bar + dots). Video area hidden when idle. Single intercom first (no tabs).
2. **Step 2**: Add multi-intercom tabs and swipe navigation in IDLE.
3. **Step 3**: Implement RING state expansion with snapshot and ring actions.
4. **Step 4**: Add ringtone playback.
5. **Step 5**: Adapt LIVE state to work within the new expandable layout (video controls overlay + persistent action bar).
6. **Step 6**: Transitions and animations polish.
