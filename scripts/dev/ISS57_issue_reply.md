Hi, thanks for the detailed report — here's where we are.

**1. The HTTP 500 on `lock.open` is fixed.** The lock entities advertised the
"open" feature but didn't implement it, so Home Assistant raised an error. That
fix will be in the next build.

**2. Why the door doesn't actually open.** Looking at how the official Netatmo
app opens the main entrance, it doesn't command any of the door-lock (BNDL)
modules — it sends an open command to the **bridge** of your system. The lock
entities you see in HA map to the BNDL actuators, which on your install aren't
the ones wired to the main entrance. That's the gap we need to close.

Before I build this into the integration, I'd like to confirm the behavior on
your hardware. Two asks:

- **Home Assistant diagnostics:** Settings → Devices & services → BTicino
  Intercom → ⋮ → *Download diagnostics*, and attach the JSON here.
- **Run the attached test script** (`iss57-main-entrance-test.zip`). It's a
  small standalone Python tool — instructions are in its README. ⚠️ It can
  physically open your door, so only run it when it's safe.

One note: I don't yet know how your entrance is wired — whether it supports
both lock and unlock, or only a momentary open. So the script can send **both**
payloads (`{"lock": false}` and `{"lock": true}`); please tell me which one
actually does something. Most intercoms just *open* and assume the door
re-locks on its own.

Once you confirm it works, I'll put together a custom test build with a proper
"Main Entrance" control for you to try.
