# Firefox WebRTC Investigation — BTicino Classe 100X

## Status: Firefox NOT SUPPORTED (device firmware limitation)

## Summary

WebRTC live video streaming works in Chrome/Chromium but fails in Firefox.
After extensive debugging (pcap captures, MOZ_LOG analysis, SDP manipulation,
4 AI agent analyses), the root cause was identified as a **device firmware bug**
that cannot be fixed from the integration side.

## Root Cause: Hardcoded RTP Payload Types

The BTicino Classe 100X (BNC1) firmware uses Chrome's default RTP payload type
mapping regardless of what is negotiated in the SDP:

| Codec | SDP Negotiated PT | Device Actually Sends | Chrome PT |
|-------|------------------|-----------------------|-----------|
| Opus  | 109 (Firefox)    | **111**               | 111       |
| H264  | 126 (Firefox)    | **109** (?)           | 109       |

Firefox receives packets with PT=111 (not in its SDP), cannot identify the
codec, and drops them. Only 3 tiny H264 packets (SPS/PPS, PT=126) reach
Firefox's video pipeline — not enough for a single frame.

## Evidence

### Wireshark/tcpdump capture (2026-04-18)

```
PT=126 (H264, correct):  3 packets, 32-55 bytes  → Firefox accepts (but too small)
PT=111 (unknown to FF):  dozens,   190 bytes each → Firefox DROPS all
```

### MOZ_LOG (`mtransport:5,RtpLogger:5`)

```
Successfully unprotected an SRTP packet of len 172  (×45 — SRTP decrypt works!)
RTCP RR Packet Send Failed                          (video RTCP broken)
Receive video RTP_PACKET  (only 1 video RTP + 2 RTCP = 3 total)
```

### getStats() (consistent across ALL attempts)

```
Inbound video:  packets=3  bytes=31  frames=0  codec=undefined  pliCount=14
Outbound audio: packets=150 bytes=450
Selected pair:  state=succeeded  bytesRx=8500  rtt=0.006
```

## What Works

- ICE connectivity (host-to-host on LAN) ✓
- DTLS handshake (confirmed via pcap: ClientHello → ServerHello → Certificate → Finished) ✓
- SRTP encryption/decryption (45 packets successfully unprotected) ✓
- SDP negotiation (device answers correctly with H264 PT=126, Opus PT=109) ✓
- Firefox H264 decode (one session DID show 4 frames at 640x480 before disconnecting) ✓

## What Was Tried (and didn't fix the PT issue)

| Attempt | Result |
|---------|--------|
| `bundlePolicy: max-bundle` | Same 3 packets |
| `bundlePolicy: max-compat` | Same 3 packets |
| `bundlePolicy: balanced` | Same 3 packets |
| RSA certificate (instead of ECDSA) | Same 3 packets |
| AudioContext running vs suspended | Same 3 packets |
| `addTrack` vs `addTransceiver` for audio | Same 3 packets |
| SDP PT remapping (109↔111, 126↔109) | Same (can't change on-wire PTs) |
| Strip `a=bundle-only` from SDP | Same 3 packets |
| Strip video SSRC from recvonly section | Not tested (lower priority) |
| go2rtc as WebRTC proxy | go2rtc #1468: async protocol not supported |

## What Was Fixed (kept in the codebase)

These fixes are valuable for general WebRTC reliability, even though they
didn't solve the Firefox-specific PT issue:

1. **ICE candidate buffering** — candidates generated before session_id were silently dropped
2. **TURN/STUN server loading** — card now fetches ICE servers from HA via `camera/webrtc/get_client_config`
3. **Signaling token refresh** — `resubscribe()` + `ensure_connected()` prevent "Invalid access_token" errors
4. **Error overlay** — user-friendly error messages shown in the card UI
5. **`rtcpMuxPolicy: 'require'`** — prevents Firefox from generating unnecessary RTCP component candidates

## go2rtc Proxy (Not Viable Currently)

go2rtc could theoretically re-packetize RTP with correct PTs, but:
- go2rtc v1.9.14 (latest) has broken `hass://` source with HA 2024.11+ async WebRTC protocol (issue #1468)
- go2rtc expects sync `result` message with answer SDP, HA sends async subscription events
- No fix available; would require patching go2rtc's Go source code

## Recommendations

1. **Document Firefox as unsupported** in README and card documentation
2. **Monitor go2rtc #1468** — when fixed, go2rtc proxy could solve the PT mismatch
3. **File BTicino/Netatmo bug report** about hardcoded PT mapping (unlikely to be fixed)
4. **Consider WebRTC-to-RTSP bridge** as future alternative (e.g., mediamtx, Pion WebRTC)
