# TODO

Ideas that nobody has started. Work in progress lives in issues: moving the protocol code into pybticino (k-the-hidden-hero/pybticino#31) and incoming calls on the EasyKit `BDIY` bridge (#72).

## Features

- Recorded video (VOD). The app replays recordings with an offer whose `session_description.type` is `"vod"` plus a `video_id`, over the same cloud WebRTC signaling. It could be a media player, or a camera with a timeline.
- Refreshing expired picture URLs through `/api/refreshbloburl`, as the app does. The call goes in pybticino first.
- The call history as a `calendar` entity, one event per call.
- A `button` entity that opens the door in one tap, next to the lock.
- A door contact `binary_sensor`, if a `BNDL` module reports the door's state and not only the lock's. Not checked yet.

## Testing

- `tests/test_live.py`, skipped unless `BTICINO_USERNAME` and `BTICINO_PASSWORD` are set, to check login, topology, status and the WebSocket subscribe against the real cloud.

The protocol notes (push types, payloads, endpoints) are in the pybticino docs: `docs/websocket-events.md`, `docs/webrtc-signaling.md` and `docs/reverse-engineering-notes.md`.
