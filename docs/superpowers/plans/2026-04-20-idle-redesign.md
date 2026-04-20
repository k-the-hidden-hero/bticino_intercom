# Card Idle Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Transform the bticino-intercom-card from a fixed-height video area (always showing a 4:3 black rectangle) to a compact card that expands only on call or ring.

**Architecture:** The card gains a new `.content-row` visible in IDLE, while `.video-area` is hidden via a `.expanded` class on `ha-card`. Clicking "Chiama" or receiving a ring adds `.expanded`, hanging up removes it. Existing WebRTC, ring overlay, and history features are preserved — they already live inside `.video-area` and simply become visible when expanded.

**Tech Stack:** Vanilla JS custom element (Lovelace card), CSS-only state transitions, HA WebSocket API

**Source file:** `/home/koma/src/bticino_ha_extras/cards/bticino-intercom-card.js` (2005 lines, single file)

**No automated tests.** This is a standalone Lovelace card — verification is manual in the browser.

**Deploy:** `cp /home/koma/src/bticino_ha_extras/cards/bticino-intercom-card.js ~/.syncthing/homeassistant_configs/www/bticino-intercom-card.js`

---

### Task 1: Compact IDLE Layout

**Files:**
- Modify: `/home/koma/src/bticino_ha_extras/cards/bticino-intercom-card.js`

Add the compact content row visible in IDLE. Hide video-area when idle via `.expanded` class.

- [ ] **Step 1: Add content-row CSS**

Add after the `.idle-name` CSS block (line ~225), before `.error-overlay`:

```css
  .content-row {
    display: flex; align-items: center; gap: 12px;
    padding: 14px 16px;
  }
  .content-icon {
    width: 40px; height: 40px; border-radius: 50%;
    background: rgba(255,255,255,0.08);
    display: flex; align-items: center; justify-content: center;
    flex-shrink: 0;
  }
  .content-icon ha-icon { --mdc-icon-size: 20px; opacity: 0.5; }
  .content-info { flex: 1; min-width: 0; }
  .content-name {
    font-size: 15px; font-weight: 600; color: var(--bti-text);
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  }
  .content-subtitle {
    font-size: 12px; color: var(--bti-text-secondary); margin-top: 2px;
  }
  .call-pill {
    background: #4caf50; color: white; border: none; border-radius: 20px;
    padding: 8px 20px; font-size: 13px; font-weight: 600; font-family: inherit;
    cursor: pointer; display: flex; align-items: center; gap: 6px;
    flex-shrink: 0; transition: background 0.15s, transform 0.1s;
  }
  .call-pill:hover { background: #43a047; }
  .call-pill:active { transform: scale(0.95); }
  .call-pill ha-icon { --mdc-icon-size: 16px; }

  ha-card:not(.expanded) .video-area { display: none; }
  ha-card.expanded .content-row { display: none; }
```

- [ ] **Step 2: Add content-row HTML to `_render()`**

In the `_render()` method (line ~669), add the content-row HTML between the SSL warning block and the `.video-area` div:

```javascript
        <div class="content-row" id="content-row">
          <div class="content-icon"><ha-icon icon="mdi:doorbell-video"></ha-icon></div>
          <div class="content-info">
            <div class="content-name">${this._esc(this._activeIntercom.name)}</div>
          </div>
          <button class="call-pill" id="call-pill"><ha-icon icon="mdi:phone"></ha-icon> Chiama</button>
        </div>
```

- [ ] **Step 3: Wire the call-pill click handler**

In `_bindEvents()` (line ~792), add after the `$('call-overlay')` listener:

```javascript
    $('call-pill')?.addEventListener('click', () => this._startCall());
```

- [ ] **Step 4: Add expand/collapse logic**

In `_startCall()` (line ~1001), add at the beginning after `if (this._playing) return;`:

```javascript
    this.shadowRoot?.querySelector('ha-card')?.classList.add('expanded');
```

In `_hangUp()` (line ~1025), add at the end before `this._setState(STATE.IDLE);`:

```javascript
    this.shadowRoot?.querySelector('ha-card')?.classList.remove('expanded');
```

In `_showRingOverlay()` (line ~1489), add at the beginning:

```javascript
    this.shadowRoot?.querySelector('ha-card')?.classList.add('expanded');
```

- [ ] **Step 5: Add collapse-if-idle helper**

Add a new method after `_showMissedCall()` (line ~1520):

```javascript
  _collapseIfIdle() {
    if (!this._playing) {
      this.shadowRoot?.querySelector('ha-card')?.classList.remove('expanded');
    }
  }
```

Call it from:
- `_dismissIncomingCall()`: add `this._collapseIfIdle();` after the classList.remove line
- `_rejectIncomingCall()`: add `this._collapseIfIdle();` at the end
- `_handleCallEvent()` in the `data.type === 'end'` branch: add `this._collapseIfIdle();` after `this._hangUp();` and also after `this._showMissedCall();`

- [ ] **Step 6: Bump version**

Change `CARD_VERSION` from `'0.1.5'` to `'0.2.0'` (line 41).

- [ ] **Step 7: Deploy and verify**

```bash
cp /home/koma/src/bticino_ha_extras/cards/bticino-intercom-card.js ~/.syncthing/homeassistant_configs/www/bticino-intercom-card.js
```

Expected:
- Card shows compact row: doorbell icon circle + intercom name + green "Chiama" pill
- Action bar visible below content row
- Tabs visible if multi-intercom
- Clicking "Chiama" → card expands showing video area with connecting animation
- Hanging up → card collapses back to compact
- Ring event → card expands showing ring overlay

- [ ] **Step 8: Commit**

```bash
cd /home/koma/src/bticino_ha_extras
git add cards/bticino-intercom-card.js
git commit -m "feat: compact idle layout — hide video area, show content row with Chiama pill"
```

---

### Task 2: Tab Restyling + Swipe Dots + Title Bar Removal

**Files:**
- Modify: `/home/koma/src/bticino_ha_extras/cards/bticino-intercom-card.js`

Restyle tabs from blue pills to text with green underline. Move swipe dots outside video-area. Remove title bar, integrate history button into content row.

- [ ] **Step 1: Restyle tab CSS**

Replace the `.tab` and `.tab.active` CSS (lines ~135-155) with:

```css
  .tab-bar {
    display: flex;
    border-bottom: 1px solid var(--bti-divider);
  }
  .tab-bar.hidden { display: none; }
  .tab {
    flex: 1;
    display: flex; align-items: center; justify-content: center; gap: 6px;
    padding: 12px 0;
    border: none;
    border-bottom: 2px solid transparent;
    background: none;
    color: var(--bti-text-secondary);
    font-size: 13px;
    font-weight: 500;
    font-family: inherit;
    cursor: pointer;
    transition: color 0.15s, border-color 0.15s;
  }
  .tab ha-icon { --mdc-icon-size: 18px; flex-shrink: 0; }
  .tab:hover { color: var(--bti-text); }
  .tab.active {
    color: #66bb6a;
    border-bottom-color: #66bb6a;
    font-weight: 600;
  }
  .tab.ring {
    color: #ff9800;
    border-bottom-color: #ff9800;
  }
  .tab.live {
    color: #ef5350;
    border-bottom-color: #ef5350;
  }
```

Remove the `gap: 4px; padding: 0 12px 8px;` from the old `.tab-bar` since it's replaced above.

- [ ] **Step 2: Move swipe dots outside video-area**

In `_render()`, move the swipe-dots div from inside `.video-area` (line ~721) to after `.action-bar` (line ~745):

```javascript
        </div><!-- end action-bar -->
        <div class="swipe-dots${showTabs ? '' : ' hidden'}" id="swipe-dots">
          ${intercoms.map((_, i) => `<div class="swipe-dot${i === this._activeIndex ? ' active' : ''}"></div>`).join('')}
        </div>
```

Update swipe-dots CSS (line ~335) — remove absolute positioning:

```css
  .swipe-dots {
    display: flex; align-items: center; justify-content: center;
    gap: 6px; padding: 8px 0;
  }
  .swipe-dots.hidden { display: none; }
  .swipe-dot {
    width: 6px; height: 6px; border-radius: 50%;
    background: rgba(255,255,255,0.35);
    transition: background 0.2s, transform 0.2s;
  }
  .swipe-dot.active {
    background: var(--bti-primary);
  }
```

- [ ] **Step 3: Remove title bar, integrate history button**

Remove the `.title-bar` HTML from `_render()` (lines ~672-676).

Add the history button into `.content-row`, between content-info and call-pill:

```javascript
        <div class="content-row" id="content-row">
          <div class="content-icon"><ha-icon icon="mdi:doorbell-video"></ha-icon></div>
          <div class="content-info">
            <div class="content-name">${this._esc(this._activeIntercom.name)}</div>
          </div>
          <button class="history-btn" id="history-btn" title="Call history"><ha-icon icon="mdi:history"></ha-icon></button>
          <button class="call-pill" id="call-pill"><ha-icon icon="mdi:phone"></ha-icon> Chiama</button>
        </div>
```

Remove `.title-bar` CSS (lines ~95-127). Keep `.status-pill` CSS for now (used by status updates).

- [ ] **Step 4: Add tab state update method**

Add a new method:

```javascript
  _updateTabStates() {
    const tabs = this.shadowRoot?.querySelectorAll('.tab');
    if (!tabs) return;
    tabs.forEach((tab, i) => {
      tab.classList.remove('ring', 'live');
      if (i === this._activeIndex) {
        if (this._state === STATE.LIVE || this._state === STATE.CONNECTING || this._state === STATE.RECONNECTING) {
          tab.classList.add('live');
        }
      }
    });
  }
```

Call `_updateTabStates()` at the end of `_setState()`.

- [ ] **Step 5: Enable swipe on content-row**

In `_bindEvents()`, add swipe binding on content-row so the user can swipe between intercoms even in idle state:

```javascript
    this._bindSwipe($('content-row'));
```

- [ ] **Step 6: Deploy and verify**

```bash
cp /home/koma/src/bticino_ha_extras/cards/bticino-intercom-card.js ~/.syncthing/homeassistant_configs/www/bticino-intercom-card.js
```

Expected:
- Tabs show as text with green underline for active tab (no blue pill background)
- Swipe dots appear below action bar (not inside video area)
- Title bar gone, history button next to Chiama pill
- Single intercom: no tabs, no dots
- Tabs turn red during LIVE state
- Swiping on content-row changes intercom

- [ ] **Step 7: Commit**

```bash
cd /home/koma/src/bticino_ha_extras
git add cards/bticino-intercom-card.js
git commit -m "feat: restyle tabs with underline, move swipe dots, remove title bar"
```

---

### Task 3: RING State with Snapshot Expansion

**Files:**
- Modify: `/home/koma/src/bticino_ha_extras/cards/bticino-intercom-card.js`

Replace the current full-screen ring overlay with the new design: snapshot in the 4:3 video area, ring actions in the action bar, orange tab.

- [ ] **Step 1: Add ring-state CSS**

Add new CSS for the ring snapshot display:

```css
  .ring-snapshot {
    position: absolute; inset: 0;
    z-index: 2;
  }
  .ring-snapshot img {
    width: 100%; height: 100%; object-fit: cover;
  }
  .ring-snapshot .ring-gradient {
    position: absolute; top: 0; left: 0; right: 0;
    height: 50%; background: linear-gradient(rgba(0,0,0,0.75), transparent);
    pointer-events: none;
  }
  .ring-snapshot .ring-label {
    position: absolute; top: 14px; left: 20px; right: 20px;
    display: flex; justify-content: space-between; align-items: center;
    pointer-events: none;
  }
  .ring-label-text { color: #ff9800; font-size: 17px; font-weight: 700; }
  .ring-label-sub { color: rgba(255,255,255,0.5); font-size: 13px; margin-top: 2px; }
  .ring-badge {
    background: rgba(255,152,0,0.2); color: #ff9800;
    border-radius: 14px; padding: 5px 12px; font-size: 12px; font-weight: 600;
    animation: ring-blink 1s infinite;
  }
  @keyframes ring-blink { 0%,100% { opacity: 1; } 50% { opacity: 0.5; } }

  ha-card.ringing {
    border: 2px solid rgba(255,152,0,0.4);
    box-shadow: 0 0 20px rgba(255,152,0,0.15);
  }

  .action-bar .ring-action { font-weight: 600; }
  .action-bar .ring-action.answer { color: #66bb6a; }
  .action-bar .ring-action.answer:hover { background: rgba(76,175,80,0.15); }
  .action-bar .ring-action.open-door { color: #42a5f5; }
  .action-bar .ring-action.open-door:hover { background: rgba(33,150,243,0.15); }
  .action-bar .ring-action.reject { color: #ef5350; }
  .action-bar .ring-action.reject:hover { background: rgba(244,67,54,0.15); }
```

- [ ] **Step 2: Rewrite `_showRingOverlay()`**

Replace the current `_showRingOverlay()` method:

```javascript
  _showRingOverlay(eventData) {
    const camIdx = this._config.intercoms.findIndex(ic => ic.camera === eventData.camera_entity_id);
    if (camIdx >= 0 && camIdx !== this._activeIndex) {
      this._activeIndex = camIdx;
    }

    this._ringData = eventData;
    const card = this.shadowRoot?.querySelector('ha-card');
    card?.classList.add('expanded', 'ringing');

    // Update tab to orange
    this._updateTabStates();

    // Insert snapshot into video-area
    const videoArea = this.shadowRoot?.getElementById('video-area');
    if (!videoArea) return;

    // Remove previous ring-snapshot if any
    videoArea.querySelector('.ring-snapshot')?.remove();

    const snapshotDiv = document.createElement('div');
    snapshotDiv.className = 'ring-snapshot';
    snapshotDiv.id = 'ring-snapshot';

    const imageUrl = eventData.snapshot_url || eventData.vignette_url;
    const moduleName = eventData.module_name || this._activeIntercom.name;

    snapshotDiv.innerHTML = `
      ${imageUrl ? '' : '<div style="display:flex;align-items:center;justify-content:center;width:100%;height:100%;background:#111"><ha-icon icon="mdi:doorbell-video" style="--mdc-icon-size:64px;opacity:0.2"></ha-icon></div>'}
      <div class="ring-gradient"></div>
      <div class="ring-label">
        <div>
          <div class="ring-label-text">Qualcuno alla porta</div>
          <div class="ring-label-sub">${this._esc(moduleName)}</div>
        </div>
        <div class="ring-badge">● RING</div>
      </div>
    `;

    videoArea.prepend(snapshotDiv);

    // Load snapshot image
    if (imageUrl) {
      this._hass.callWS({ type: 'auth/sign_path', path: imageUrl, expires: 60 })
        .then(({ path }) => {
          const img = document.createElement('img');
          img.src = path;
          img.alt = '';
          snapshotDiv.insertBefore(img, snapshotDiv.firstChild);
        })
        .catch(() => {});
    }

    // Replace action bar with ring actions
    this._showRingActions();
  }
```

- [ ] **Step 3: Add ring actions in action bar**

Add method to swap action bar content:

```javascript
  _showRingActions() {
    const bar = this.shadowRoot?.getElementById('action-bar');
    if (!bar) return;
    this._savedActionBarHTML = bar.innerHTML;
    bar.innerHTML = `
      <button class="action-btn ring-action answer" id="ring-answer">
        <ha-icon icon="mdi:phone"></ha-icon>
        <span class="action-label">Rispondi</span>
      </button>
      <button class="action-btn ring-action open-door" id="ring-open">
        <ha-icon icon="mdi:door-open"></ha-icon>
        <span class="action-label">Apri</span>
      </button>
      <button class="action-btn ring-action reject" id="ring-reject">
        <ha-icon icon="mdi:phone-hangup"></ha-icon>
        <span class="action-label">Rifiuta</span>
      </button>
    `;
    bar.querySelector('#ring-answer')?.addEventListener('click', () => this._answerIncomingCall());
    bar.querySelector('#ring-open')?.addEventListener('click', () => this._openDoorDuringRing());
    bar.querySelector('#ring-reject')?.addEventListener('click', () => this._rejectIncomingCall());
  }

  _restoreActionBar() {
    const bar = this.shadowRoot?.getElementById('action-bar');
    if (!bar || !this._savedActionBarHTML) return;
    bar.innerHTML = this._savedActionBarHTML;
    this._savedActionBarHTML = null;
    // Re-bind action buttons
    bar.querySelectorAll('.action-btn[data-action-idx]').forEach(btn => {
      btn.addEventListener('click', (e) => {
        e.stopPropagation();
        this._executeAction(parseInt(btn.dataset.actionIdx, 10), btn);
      });
    });
    this._updateActionStates();
  }

  _openDoorDuringRing() {
    // Find the first lock action in the active intercom
    const lockAction = this._activeIntercom.actions.find(a => a.entity?.startsWith('lock.'));
    if (lockAction && this._hass) {
      const [domain, service] = (lockAction.service || 'lock.unlock').split('.');
      this._hass.callService(domain, service, lockAction.service_data || {}, { entity_id: lockAction.entity });
    }
  }
```

- [ ] **Step 4: Update `_updateTabStates()` for ring state**

Modify `_updateTabStates()` to handle the ringing tab:

```javascript
  _updateTabStates() {
    const tabs = this.shadowRoot?.querySelectorAll('.tab');
    if (!tabs) return;
    const isRinging = this.shadowRoot?.querySelector('ha-card')?.classList.contains('ringing');
    tabs.forEach((tab, i) => {
      tab.classList.remove('ring', 'live');
      const origName = this._config.intercoms[i]?.name || '';
      if (i === this._activeIndex && isRinging) {
        tab.classList.add('ring');
        tab.textContent = `🔔 ${origName}`;
      } else if (i === this._activeIndex && (this._state === STATE.LIVE || this._state === STATE.CONNECTING || this._state === STATE.RECONNECTING)) {
        tab.classList.add('live');
        tab.textContent = `● ${origName}`;
      } else {
        tab.textContent = origName;
      }
    });
  }
```

Note: this simplified approach replaces tab `textContent`. If the tab had an icon (`ha-icon`), the icon-based rendering from `_render()` would need to be replicated. For now, text-only tabs are sufficient — icons can be added back in polish.

- [ ] **Step 5: Update dismiss/reject/end to clean up ring state**

Update `_answerIncomingCall()`:

```javascript
  _answerIncomingCall() {
    this._clearRingState();
    this._startCall();
  }
```

Update `_rejectIncomingCall()`:

```javascript
  _rejectIncomingCall() {
    this._clearRingState();
    this._collapseIfIdle();
    const entryId = this._getConfigEntryId();
    if (entryId && this._hass) {
      this._hass.callService('bticino_intercom', 'reject_call', { entry_id: entryId });
    }
  }
```

Update `_dismissIncomingCall()`:

```javascript
  _dismissIncomingCall() {
    this._clearRingState();
    this._collapseIfIdle();
  }
```

Add `_clearRingState()`:

```javascript
  _clearRingState() {
    this.shadowRoot?.querySelector('ha-card')?.classList.remove('ringing');
    this.shadowRoot?.getElementById('ring-snapshot')?.remove();
    this.shadowRoot?.getElementById('ring-overlay')?.classList.remove('open');
    this._restoreActionBar();
    this._updateTabStates();
    this._ringData = null;
  }
```

Update the `'end'` branch in `_handleCallEvent()` to also call `_clearRingState()`:

```javascript
    } else if (data.type === 'end') {
      if (data.session_id !== this._ringSessionId) return;
      this._ringSessionId = null;
      const wasRinging = this.shadowRoot?.querySelector('ha-card')?.classList.contains('ringing');
      this._clearRingState();
      if (wasRinging) {
        this._showMissedCall();
      }
      if (this._state === STATE.LIVE) {
        this._hangUp();
      }
      this._collapseIfIdle();
    }
```

- [ ] **Step 6: Deploy and verify**

```bash
cp /home/koma/src/bticino_ha_extras/cards/bticino-intercom-card.js ~/.syncthing/homeassistant_configs/www/bticino-intercom-card.js
```

Verify by firing a test ring event in browser console:
```javascript
hass.connection.sendMessage({type: 'fire_event', event_type: 'bticino_intercom_call', event_data: {type: 'ring', camera_entity_id: 'camera.YOUR_CAMERA', session_id: 'test-1', module_name: 'Citofono Strada'}});
```

Expected:
- Card expands showing 4:3 area with snapshot (or placeholder icon if no image)
- Orange border glow on card
- Tab turns orange with 🔔
- "● RING" badge blinks at top of snapshot
- Action bar shows: Rispondi (green) / Apri (blue) / Rifiuta (red)
- Tap "Rifiuta" → card collapses back to compact, border glow gone
- Fire end event → missed call banner appears, then card collapses

- [ ] **Step 7: Commit**

```bash
cd /home/koma/src/bticino_ha_extras
git add cards/bticino-intercom-card.js
git commit -m "feat: ring state with snapshot expansion, ring actions in action bar, orange tab"
```

---

### Task 4: Ringtone Playback

**Files:**
- Modify: `/home/koma/src/bticino_ha_extras/cards/bticino-intercom-card.js`

Play `doorbell.wav` (already registered as a static path at `/local/doorbell.wav`) when a ring event arrives.

- [ ] **Step 1: Add ringtone properties to constructor**

In the constructor (line ~558), add:

```javascript
    this._ringtoneAudio = null;
```

- [ ] **Step 2: Add ringtone play/stop methods**

Add after `_clearRingState()`:

```javascript
  _playRingtone() {
    this._stopRingtone();
    try {
      this._ringtoneAudio = new Audio('/local/doorbell.wav');
      this._ringtoneAudio.loop = true;
      this._ringtoneAudio.play().catch(() => {});
    } catch (_) {}
  }

  _stopRingtone() {
    if (this._ringtoneAudio) {
      this._ringtoneAudio.pause();
      this._ringtoneAudio.currentTime = 0;
      this._ringtoneAudio = null;
    }
  }
```

- [ ] **Step 3: Wire ringtone to ring events**

In `_showRingOverlay()`, add at the end:

```javascript
    this._playRingtone();
```

In `_clearRingState()`, add:

```javascript
    this._stopRingtone();
```

In `disconnectedCallback()`, add:

```javascript
    this._stopRingtone();
```

- [ ] **Step 4: Deploy and verify**

```bash
cp /home/koma/src/bticino_ha_extras/cards/bticino-intercom-card.js ~/.syncthing/homeassistant_configs/www/bticino-intercom-card.js
```

Fire a test ring event. Expected:
- Doorbell sound plays in loop when ring arrives
- Sound stops when Rispondi/Apri/Rifiuta is tapped
- Sound stops when call ends (end event)
- No errors in console if audio can't play (autoplay policy)

- [ ] **Step 5: Commit**

```bash
cd /home/koma/src/bticino_ha_extras
git add cards/bticino-intercom-card.js
git commit -m "feat: play doorbell.wav ringtone on incoming call"
```

---

### Task 5: LIVE State in Expandable Layout

**Files:**
- Modify: `/home/koma/src/bticino_ha_extras/cards/bticino-intercom-card.js`

Ensure the LIVE state works correctly within the expand/collapse layout. Action bar shows normal actions during LIVE (for quick door open while talking). Tab turns red.

- [ ] **Step 1: Restore action bar on LIVE state**

In `_answerIncomingCall()`, the action bar is already restored via `_clearRingState()`. But when entering LIVE from IDLE (tapping "Chiama"), the action bar is already showing normal actions. No change needed here.

Verify that `_hangUp()` restores normal action bar if it was replaced (edge case: ring → answer → hangup). Add to `_hangUp()` before removing expanded class:

```javascript
    this._clearRingState();
```

This is safe to call even if not ringing — it no-ops if no saved HTML.

- [ ] **Step 2: Update tab to red during LIVE**

`_updateTabStates()` already handles this from Task 2 — it checks `this._state === STATE.LIVE` and adds `.live` class. Verify that `_setState()` calls `_updateTabStates()`.

If not already done, ensure `_setState()` ends with:

```javascript
    this._updateTabStates();
```

- [ ] **Step 3: Ensure video-area shows properly during LIVE**

The `.expanded` class is added in `_startCall()` (Task 1). The video area appears. The existing connecting overlay, video, and video controls all work inside it. No structural changes needed.

Verify the full flow: Chiama → connecting animation → LIVE → video + controls overlay → Hangup → collapse.

- [ ] **Step 4: Deploy and verify**

```bash
cp /home/koma/src/bticino_ha_extras/cards/bticino-intercom-card.js ~/.syncthing/homeassistant_configs/www/bticino-intercom-card.js
```

Test the full call flow:
1. Compact IDLE → tap "Chiama" → card expands, connecting animation
2. Connection established → LIVE, tab turns red "● Strada"
3. Action bar shows normal actions (Apri, Luce scale, etc.) — tap Apri while live
4. Tap Hangup → card collapses to compact, tab returns to green

Test ring → answer flow:
1. Fire ring event → card expands with snapshot, orange tab, ring actions
2. Tap "Rispondi" → ring state cleared, WebRTC connects
3. LIVE → red tab, normal actions in bar, video controls overlay
4. Hangup → collapse

- [ ] **Step 5: Commit**

```bash
cd /home/koma/src/bticino_ha_extras
git add cards/bticino-intercom-card.js
git commit -m "feat: LIVE state within expandable layout, tab indicators"
```

---

### Task 6: Transitions and Animations

**Files:**
- Modify: `/home/koma/src/bticino_ha_extras/cards/bticino-intercom-card.js`

Add smooth height animation for expand/collapse and crossfade for snapshot → video.

- [ ] **Step 1: Add expand animation CSS**

Wrap `.video-area` in a `.media-wrapper` for animated height. Replace the hide/show CSS from Task 1:

Remove:
```css
  ha-card:not(.expanded) .video-area { display: none; }
```

Add:
```css
  .media-wrapper {
    display: grid;
    grid-template-rows: 0fr;
    transition: grid-template-rows 0.35s ease;
  }
  .media-wrapper > .video-area {
    overflow: hidden;
  }
  ha-card.expanded .media-wrapper {
    grid-template-rows: 1fr;
  }
  ha-card.expanded .content-row { display: none; }
```

- [ ] **Step 2: Add media-wrapper to HTML template**

In `_render()`, wrap the `.video-area` div:

```javascript
        <div class="media-wrapper">
          <div class="video-area" id="video-area">
            ...existing video-area contents...
          </div>
        </div>
```

- [ ] **Step 3: Add snapshot → video crossfade**

In `_answerIncomingCall()`, after `_clearRingState()` but before `_startCall()`, the ring-snapshot is removed. The video area shows the idle overlay briefly, then the connecting overlay appears. This is already a smooth-enough transition.

For a proper crossfade, we'd keep the snapshot visible and fade it out once video starts playing. Add to the `onconnectionstatechange` handler (inside `_connect()`), in the `connected` branch:

```javascript
          // Fade out any lingering ring snapshot
          const snapshot = this.shadowRoot?.getElementById('ring-snapshot');
          if (snapshot) {
            snapshot.style.transition = 'opacity 0.5s ease';
            snapshot.style.opacity = '0';
            setTimeout(() => snapshot.remove(), 500);
          }
```

And in `_answerIncomingCall()`, DON'T remove the snapshot via `_clearRingState()`. Instead, only clear the ring state (border, tab, actions, ringtone) but keep the snapshot visible:

```javascript
  _answerIncomingCall() {
    this.shadowRoot?.querySelector('ha-card')?.classList.remove('ringing');
    this._stopRingtone();
    this._restoreActionBar();
    this._updateTabStates();
    this._ringData = null;
    // Keep ring-snapshot visible for crossfade — removed when video connects
    this._startCall();
  }
```

- [ ] **Step 4: Add missed-call banner transition**

Update `_showMissedCall()` to work outside video-area. Since the missed banner is inside video-area and the card may collapse, change it to use the content-row area instead:

```javascript
  _showMissedCall() {
    this._clearRingState();
    // Brief "Missed call" text in content-name
    const nameEl = this.shadowRoot?.querySelector('.content-name');
    if (nameEl) {
      const original = nameEl.textContent;
      nameEl.textContent = '📞 Chiamata persa';
      nameEl.style.color = '#ffa726';
      setTimeout(() => {
        nameEl.textContent = original;
        nameEl.style.color = '';
      }, 5000);
    }
  }
```

- [ ] **Step 5: Deploy and verify**

```bash
cp /home/koma/src/bticino_ha_extras/cards/bticino-intercom-card.js ~/.syncthing/homeassistant_configs/www/bticino-intercom-card.js
```

Expected:
- Card expand/collapse is animated (smooth height growth/shrink)
- Ring → Answer: snapshot stays visible, fades out when live video starts
- Call ends during ring: card collapses smoothly, "Chiamata persa" shows briefly in content row
- No layout jumps — smooth transitions throughout

- [ ] **Step 6: Commit**

```bash
cd /home/koma/src/bticino_ha_extras
git add cards/bticino-intercom-card.js
git commit -m "feat: smooth expand/collapse animation, snapshot crossfade, missed call banner"
```
