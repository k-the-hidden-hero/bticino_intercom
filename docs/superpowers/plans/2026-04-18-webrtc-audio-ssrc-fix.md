# WebRTC Audio SSRC Injection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix audio silence and ~10s session drops by injecting a dummy audio SSRC into the browser's SDP offer, matching what the BTicino mobile app does natively.

**Architecture:** The BTicino device requires a local audio SSRC in the SDP offer to enable audio transmission. The official app adds a real audio track (with mic) before creating the offer, generating a natural SSRC. Since the browser's HA camera player doesn't have microphone access, we inject a synthetic SSRC into the audio m-section of the SDP. Two approaches to test: (A) `sendrecv` + SSRC (device may expect RTP and timeout), (B) `recvonly` + SSRC (device may accept receive-only with a valid endpoint).

**Tech Stack:** SDP string manipulation in `camera.py`, pybticino signaling.

---

## File Structure

| File | Action | Change |
|------|--------|--------|
| `custom_components/bticino_intercom/camera.py` | Modify | Update `_enable_audio_sendrecv()` to inject SSRC. Add `_inject_audio_ssrc()` method. |
| `tests/test_camera.py` | Modify | Tests for SSRC injection. |

---

### Task 1: Inject dummy audio SSRC into offer SDP

**Files:**
- Modify: `custom_components/bticino_intercom/camera.py`
- Test: `tests/test_camera.py`

- [ ] **Step 1: Write the failing test**

```python
class TestInjectAudioSsrc:
    """Test audio SSRC injection into SDP offer."""

    def test_ssrc_added_to_audio_section(self) -> None:
        """Audio section should get a synthetic SSRC."""
        sdp = (
            "v=0\r\n"
            "m=audio 9 UDP/TLS/RTP/SAVPF 111\r\n"
            "a=mid:0\r\n"
            "a=recvonly\r\n"
            "m=video 9 UDP/TLS/RTP/SAVPF 96\r\n"
            "a=mid:1\r\n"
            "a=recvonly\r\n"
        )
        result = BticinoWebRTCCamera._inject_audio_ssrc(sdp)
        lines = result.split("\r\n")
        audio_idx = next(i for i, l in enumerate(lines) if l.startswith("m=audio"))
        video_idx = next(i for i, l in enumerate(lines) if l.startswith("m=video"))
        audio_section = "\r\n".join(lines[audio_idx:video_idx])
        assert "a=ssrc:" in audio_section
        assert "cname:" in audio_section

    def test_ssrc_not_added_to_video_section(self) -> None:
        """Video section should NOT get an extra SSRC."""
        sdp = (
            "v=0\r\n"
            "m=audio 9 UDP/TLS/RTP/SAVPF 111\r\n"
            "a=mid:0\r\n"
            "a=recvonly\r\n"
            "m=video 9 UDP/TLS/RTP/SAVPF 96\r\n"
            "a=mid:1\r\n"
            "a=recvonly\r\n"
        )
        result = BticinoWebRTCCamera._inject_audio_ssrc(sdp)
        lines = result.split("\r\n")
        video_idx = next(i for i, l in enumerate(lines) if l.startswith("m=video"))
        video_section = "\r\n".join(lines[video_idx:])
        # Video should NOT have our injected SSRC
        assert "cname:bticino-ha" not in video_section

    def test_ssrc_not_duplicated_if_already_present(self) -> None:
        """If audio section already has an SSRC, don't add another."""
        sdp = (
            "v=0\r\n"
            "m=audio 9 UDP/TLS/RTP/SAVPF 111\r\n"
            "a=mid:0\r\n"
            "a=sendrecv\r\n"
            "a=ssrc:99999 cname:existing\r\n"
            "m=video 9 UDP/TLS/RTP/SAVPF 96\r\n"
            "a=mid:1\r\n"
            "a=recvonly\r\n"
        )
        result = BticinoWebRTCCamera._inject_audio_ssrc(sdp)
        assert result.count("a=ssrc:") == 1  # Only the existing one
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_camera.py::TestInjectAudioSsrc -v`
Expected: FAIL — `_inject_audio_ssrc` not defined.

- [ ] **Step 3: Implement SSRC injection**

Add to `BticinoWebRTCCamera` in `camera.py`:

```python
@staticmethod
def _inject_audio_ssrc(sdp: str) -> str:
    """Inject a synthetic audio SSRC into the SDP if none exists.

    The BTicino device requires a local audio SSRC in the offer to
    enable audio transmission. The official mobile app adds a real
    audio track (with microphone) which generates a natural SSRC.
    Since the browser doesn't have microphone access, we inject a
    synthetic one.

    The SSRC value (1000) is arbitrary — the device just needs to see
    a valid sender endpoint. The cname follows the app's format.

    Only injects into the audio m-section, and only if no SSRC is
    already present (to avoid duplicates when a two-way audio card
    provides its own track).
    """
    lines = sdp.split("\r\n")
    result = []
    in_audio = False
    audio_has_ssrc = False

    # First pass: check if audio section already has an SSRC
    for line in lines:
        if line.startswith("m=audio"):
            in_audio = True
        elif line.startswith("m="):
            in_audio = False
        if in_audio and line.startswith("a=ssrc:"):
            audio_has_ssrc = True
            break

    if audio_has_ssrc:
        return sdp

    # Second pass: inject SSRC at end of audio section
    in_audio = False
    for line in lines:
        if line.startswith("m=audio"):
            in_audio = True
        elif line.startswith("m=") and in_audio:
            # End of audio section — inject SSRC before next m-line
            result.append("a=ssrc:1000 cname:bticino-ha")
            result.append("a=ssrc:1000 msid:bticino-intercom audio0")
            in_audio = False
        result.append(line)

    # If audio was the last section (no video after), inject at end
    if in_audio:
        # Insert before the final empty line
        if result and result[-1] == "":
            result.insert(-1, "a=ssrc:1000 cname:bticino-ha")
            result.insert(-1, "a=ssrc:1000 msid:bticino-intercom audio0")
        else:
            result.append("a=ssrc:1000 cname:bticino-ha")
            result.append("a=ssrc:1000 msid:bticino-intercom audio0")

    return "\r\n".join(result)
```

- [ ] **Step 4: Wire into the offer flow**

In `async_handle_async_webrtc_offer()`, after `_enable_audio_sendrecv()`, add SSRC injection:

```python
# Enable bidirectional audio — the device only sends audio when
# it sees sendrecv (like the mobile app does)
modified_offer = self._enable_audio_sendrecv(offer_sdp)
audio_was_rewritten = modified_offer != offer_sdp
offer_sdp = modified_offer

# Inject synthetic audio SSRC — the device needs a sender endpoint
# to enable audio transmission (the mobile app has a real mic track)
offer_sdp = self._inject_audio_ssrc(offer_sdp)
```

- [ ] **Step 5: Run tests**

Run: `pytest tests/ -v`
Expected: All pass.

- [ ] **Step 6: Commit**

```bash
git add custom_components/bticino_intercom/camera.py tests/test_camera.py
git commit -m "feat: inject synthetic audio SSRC into SDP offer

The BTicino device requires a local audio SSRC in the SDP offer to
enable audio transmission. The official app adds a real audio track
(microphone) which generates a natural SSRC. Since the HA browser
player doesn't have mic access, we inject a synthetic SSRC.

This should fix both audio silence and ~10s session drops (the device
likely terminates when it doesn't see a valid audio sender endpoint).

Skips injection if an SSRC already exists (two-way audio cards)."
```

---

### Task 2: Test on real device and iterate

This task cannot be automated — requires deploying to HA and observing behavior.

- [ ] **Step 1: Deploy to HA**

- [ ] **Step 2: Open Citofono Strada camera**

- [ ] **Step 3: Check audio** — do you hear ambient sound?

- [ ] **Step 4: Check session duration** — does it last more than 10 seconds?

- [ ] **Step 5: If still no audio, try approach B**

Approach B: keep `recvonly` in the offer (don't rewrite to `sendrecv`) but still inject the SSRC. This tells the device "I have an audio endpoint but I only want to receive". Change `_enable_audio_sendrecv` to NOT rewrite, just inject SSRC.

- [ ] **Step 6: If neither works, check the device's answer SDP**

The device answer should have `sendonly` or `sendrecv` for audio. If it still has `recvonly` despite our SSRC, the device may need additional triggering.

- [ ] **Step 7: Collect results and iterate**
