# ISS-57 — Main-Entrance Open Test Script — Design

**Date:** 2026-06-01
**Author:** Andrea Cervesato
**Status:** Draft — awaiting review
**Issue:** [#57](https://github.com/.../issues/57) — door open does nothing / HTTP 500 on `lock.open`

## Background

Issue #57 has two distinct problems:

1. **HTTP 500 on `lock.open`** — both `BticinoLock` and `BticinoLightAsLock`
   advertise `LockEntityFeature.OPEN` but never implemented `async_open`, so
   Home Assistant raised `NotImplementedError`. **Already fixed** on
   `dev/webrtc` (added `async_open` delegating to `async_unlock`, 2 tests,
   11/11 green). Not part of this design.

2. **Functional gap — the main entrance can't be opened.** APK analysis
   (high confidence) shows the Netatmo app's "OPEN_LOCK" button targets the
   **bridge module id** (BNC1/BNCX) with a plain REST `setstate {"lock": false}`,
   *not* any of the reporter's BNDL door-lock modules. The reporter's 3 BNDL
   modules are auxiliary actuators not wired to the main entrance. The current
   integration never sends a command to the bridge id, so the main door can't
   be opened from HA.

The APK finding is a **hypothesis** — the literal click-listener binding was
not extracted. Sending `setstate {"lock": false}` to the bridge id
**physically opens a real door**, so we must not build the feature blind. We
verify the behavior with a cheap, user-run standalone script first.

## Goal

Ship a **standalone, interactive Python script** (using `pybticino`) that the
reporter runs by hand to confirm whether opening via the **bridge id** physically
opens their main entrance. Deliver it as a ZIP attached to issue #57, with a
step-by-step README covering (a) producing a HA diagnostics dump and (b) running
the script. If the reporter confirms it works, we have everything needed to build
the real "Main Entrance" lock feature.

## Non-goals

- Implementing the actual Main Entrance lock entity (deferred until the reporter
  confirms the script works; tracked separately, proposed tag
  `v2.0.0-rc14-test57`).
- Any change to the `bticino_intercom` component in this design.
- Modifying production HA (forbidden without explicit permission).

## Decisions (from prior discussion)

| Question | Choice |
|---|---|
| Location & name | `scripts/dev/ISS57_test_main_entrance_open.py` |
| Convention going forward | New `scripts/dev/` folder for dev/debug scripts; `ISS<issue-number>_` filename prefix |
| Credentials input | **Interactive** prompts (email + `getpass` password), not env vars — easier for a non-technical reporter, no secrets left in shell history |
| Home selection | Auto-detect; if multiple homes, list and prompt |
| Safety | Print full module inventory, then an explicit **typed confirmation** before each send (it commands a real door) |
| Target | The **bridge id** with `set_module_state(module_id=bridge_id, bridge_id=bridge_id, state={...})` |
| Both payloads | The script can send **unlock** `{"lock": false}` *and* **lock** `{"lock": true}` — we don't know whether the reporter's main entrance supports both or is a momentary open-only |
| Interaction model | **Interactive loop (REPL)**: after auth + inventory, keep offering unlock / lock / quit; do not exit after one command |
| Escape hatch | Optional `--module-id <id>` to override the target (so the reporter can also test a specific BNDL if asked) |
| Base pattern | `pybticino/examples/unlock_door.py` |

## Script behavior

Based on `examples/unlock_door.py`, adapted to interactive + bridge-targeting:

```
SETUP (once):
1. Prompt for email (input) and password (getpass).
2. AuthHandler(email, password) → AsyncAccount(auth).
3. await account.async_update_topology().
4. Resolve home:
     - 0 homes → error + exit
     - 1 home  → use it
     - N homes → list (name + id) and prompt for choice
5. Identify the bridge module:
     - match module.type in {"BNC1", "BNCX"}
     - if --module-id given, that overrides the target instead
6. Print the FULL module inventory for the chosen home:
     name | type | id | reachable  (+ note which one is the bridge / chosen target)
   This doubles as a second data source alongside the HA diagnostics dump.
7. Determine timezone from home.raw_data["timezone"] (warn if missing).

LOOP (until user quits):
8. Present a menu:
     [u] send UNLOCK / open  →  state={"lock": false}
     [l] send LOCK / close    →  state={"lock": true}
     [q] quit
9. On u or l: show the exact command (module_id=<target>, bridge_id=<bridge>,
   state=...), then a SAFETY GATE — type `YES` to send, anything else cancels
   and returns to the menu (it commands a real door).
10. await account.async_set_module_state(home_id, module_id=target,
        state=<payload>, timezone=tz, bridge_id=bridge_id); print result.
11. Loop back to the menu (the session stays open for repeated tries).
12. On q (or Ctrl-C): break the loop.

TEARDOWN:
13. finally: await auth.close_session().
```

Error handling mirrors the example: catch `AuthError`, `ApiError`
(status + message), generic `Exception`. An `ApiError` on a single send is
printed and the loop continues (so a failed lock doesn't kill the session);
auth/teardown errors still exit. Session always closed in `finally`.

### CLI

```
python3 ISS57_test_main_entrance_open.py [--module-id <id>]
```

No positional args (unlike the example). `--module-id` optional override.

## ZIP package contents

Attached to issue #57:

```
iss57-main-entrance-test/
├── ISS57_test_main_entrance_open.py
└── README.md
```

### README.md (English) — outline

1. **What this is / safety warning** — it can physically open your main door;
   run only when you are present and it is safe.
2. **Prerequisites** — Python 3.11+, `pip install pybticino`.
3. **Step 1 — Download HA diagnostics** (so we can cross-check your modules):
   Settings → Devices & services → BTicino Intercom → ⋮ → *Download diagnostics*;
   attach the JSON to the issue.
4. **Step 2 — Run the script** — `python3 ISS57_test_main_entrance_open.py`,
   enter email/password, review the printed module list, then use the menu:
   `u` to send unlock/open, `l` to send lock/close, `q` to quit. Each send asks
   you to type `YES` first. The script keeps looping so you can try both.
5. **Step 3 — Report back** — did the main entrance physically open on `u`?
   Did `l` do anything? Paste the script's output (it does not contain your
   password).

## Issue #57 reply (English) — outline

- Thank the reporter; summarize findings.
- Announce the **HTTP 500 fix** (`lock.open` now implemented) landing in the next build.
- Explain the **BNDL vs bridge** distinction: the app opens the main entrance by
  commanding the *bridge*, not the door-lock modules — which is why the existing
  lock entities don't open the main door.
- Ask for two things: (1) the **HA diagnostics** JSON, (2) the **result of the
  attached test script** (attach ZIP). Note the safety warning.
- **Lock vs unlock note:** we don't know how your main entrance is wired —
  whether it supports both lock and unlock, or is open-only — so the script can
  send **both** payloads (`{"lock": false}` and `{"lock": true}`). Let us know
  which one(s) actually do something. Most intercoms just *open* and assume the
  door re-closes/re-locks on its own.
- Set expectation: if the script confirms it works, a custom test build with a
  proper "Main Entrance" lock follows.

## Verification

- Script is standalone: `python3 -c "import ast; ast.parse(open('scripts/dev/ISS57_test_main_entrance_open.py').read())"` parses clean.
- `uvx ruff check scripts/dev/ISS57_test_main_entrance_open.py` passes.
- Manual dry path: run with bad credentials → clean `AuthError`, session closed,
  no traceback leak.
- Cannot fully verify the physical open without the reporter's hardware — that is
  the whole point of shipping the script.

## Open questions for review

1. ZIP top-level folder name `iss57-main-entrance-test` — ok?

**Resolved:** the script is an interactive **loop** offering both unlock and lock
each cycle (not one-shot); safety gate is typing `YES` per send; the issue reply
carries the lock-vs-unlock explanation.
