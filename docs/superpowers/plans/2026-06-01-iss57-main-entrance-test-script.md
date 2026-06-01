# ISS-57 Main-Entrance Open Test Script — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a standalone interactive `pybticino` script (+ ZIP/README + issue reply) that lets the issue-#57 reporter verify whether commanding the bridge id opens their main entrance.

**Architecture:** Single self-contained Python file under `scripts/dev/`, modeled on `pybticino/examples/unlock_door.py`. Interactive: prompts for credentials, auto-detects home + bridge, prints the module inventory, then loops a menu offering unlock/lock/quit with a per-send typed confirmation. Targets the bridge module id with `async_set_module_state(state={"lock": false|true})`. No tests (interactive script against a live cloud API); verification is parse + ruff + a bad-credentials dry run.

**Tech Stack:** Python 3.11+, `pybticino` (AuthHandler, AsyncAccount), asyncio, getpass, argparse.

---

### Task 1: Create the test script

**Files:**
- Create: `scripts/dev/ISS57_test_main_entrance_open.py`

- [ ] **Step 1: Create the directory**

Run: `mkdir -p scripts/dev`

- [ ] **Step 2: Write the full script**

Write `scripts/dev/ISS57_test_main_entrance_open.py` with exactly this content:

```python
#!/usr/bin/env python3
"""Issue #57 — test whether commanding the bridge opens the main entrance.

Interactive helper built on pybticino. It authenticates with your
BTicino/Netatmo (Home + Security) account, lists the modules in your home,
and then lets you send an UNLOCK ({"lock": false}) or LOCK ({"lock": true})
command to the *bridge* module — which is how the official app opens the
main entrance.

SAFETY: sending UNLOCK can physically open a real door. Only run this when
you are present and it is safe to do so. Each send asks you to type YES first.

Usage:
    pip install pybticino
    python3 ISS57_test_main_entrance_open.py [--module-id <id>]

No credentials are read from the environment or stored on disk; you are
prompted for them and the password is never echoed.
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import logging
import sys

from pybticino import ApiError, AsyncAccount, AuthError, AuthHandler

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
_LOGGER = logging.getLogger("iss57")

BRIDGE_TYPES = {"BNC1", "BNCX"}


def _select_home(account: AsyncAccount):
    """Return the chosen Home object, prompting if there is more than one."""
    homes = list(account.homes.values())
    if not homes:
        _LOGGER.error("No homes found on this account.")
        sys.exit(1)
    if len(homes) == 1:
        return homes[0]
    print("\nMultiple homes found:")
    for i, home in enumerate(homes):
        print(f"  [{i}] {home.name} (id={home.id})")
    while True:
        choice = input("Choose a home by number: ").strip()
        if choice.isdigit() and 0 <= int(choice) < len(homes):
            return homes[int(choice)]
        print("Invalid choice, try again.")


def _find_bridge_id(home) -> str | None:
    """Return the id of the bridge module (BNC1/BNCX) in the home, if any."""
    for module in home.modules:
        if module.type in BRIDGE_TYPES:
            return module.id
    return None


def _print_inventory(home, target_id: str, bridge_id: str | None) -> None:
    """Print every module in the home, flagging the bridge and the target."""
    print(f"\nModules in home '{home.name}' (id={home.id}):")
    print(f"  {'name':<28} {'type':<8} {'id':<28} flags")
    for module in home.modules:
        flags = []
        if module.id == bridge_id:
            flags.append("BRIDGE")
        if module.id == target_id:
            flags.append("TARGET")
        print(
            f"  {module.name:<28} {module.type:<8} {module.id:<28} {','.join(flags)}"
        )
    print()


async def _send(account, home, target_id, bridge_id, tz, payload) -> None:
    """Send a single setstate command to the target via the bridge."""
    print(f"\nAbout to send: module_id={target_id} bridge_id={bridge_id} state={payload}")
    confirm = input("Type YES to send (anything else cancels): ").strip()
    if confirm != "YES":
        print("Cancelled.\n")
        return
    try:
        result = await account.async_set_module_state(
            home_id=home.id,
            module_id=target_id,
            state=payload,
            timezone=tz,
            bridge_id=bridge_id,
        )
        print(f"Sent. API result: {result}\n")
    except ApiError as err:
        print(f"API error: status={err.status_code} message={err.error_message}\n")


async def main() -> None:
    """Run the interactive bridge open/close tester."""
    parser = argparse.ArgumentParser(description="ISS-57 main-entrance open tester")
    parser.add_argument(
        "--module-id",
        help="Override the target module id (defaults to the bridge id).",
    )
    args = parser.parse_args()

    email = input("BTicino/Netatmo email: ").strip()
    password = getpass.getpass("Password: ")

    auth = AuthHandler(email, password)
    account = AsyncAccount(auth)
    try:
        print("Fetching topology...")
        await account.async_update_topology()

        home = _select_home(account)
        bridge_id = _find_bridge_id(home)
        target_id = args.module_id or bridge_id

        if target_id is None:
            _LOGGER.error(
                "No bridge (BNC1/BNCX) found and no --module-id given; nothing to target."
            )
            sys.exit(1)

        tz = home.raw_data.get("timezone")
        if not tz:
            _LOGGER.warning("No timezone found for the home; proceeding without it.")

        _print_inventory(home, target_id, bridge_id)
        print(f"Target module id: {target_id}")
        print(f"Bridge module id: {bridge_id}")

        while True:
            print("Menu:  [u] unlock/open ({\"lock\": false})   "
                  "[l] lock/close ({\"lock\": true})   [q] quit")
            choice = input("> ").strip().lower()
            if choice == "u":
                await _send(account, home, target_id, bridge_id, tz, {"lock": False})
            elif choice == "l":
                await _send(account, home, target_id, bridge_id, tz, {"lock": True})
            elif choice == "q":
                break
            else:
                print("Unknown choice.")
    except AuthError:
        _LOGGER.exception("Authentication failed — check email/password.")
    except (KeyboardInterrupt, EOFError):
        print("\nInterrupted.")
    except Exception:
        _LOGGER.exception("Unexpected error.")
    finally:
        await auth.close_session()
        print("Session closed.")


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 3: Verify it parses**

Run: `.venv/bin/python -c "import ast; ast.parse(open('scripts/dev/ISS57_test_main_entrance_open.py').read()); print('OK')"`
Expected: `OK`

- [ ] **Step 4: Lint**

Run: `uvx ruff check scripts/dev/ISS57_test_main_entrance_open.py`
Expected: `All checks passed!` (fix any reported issues, e.g. line length, then re-run)

- [ ] **Step 5: Bad-credentials dry run (no hardware needed)**

Run: `printf 'not@an.account\nwrongpw\n' | .venv/bin/python scripts/dev/ISS57_test_main_entrance_open.py`
Expected: logs an authentication failure (`AuthError`) or API error, then prints `Session closed.` with no unhandled traceback. (The credentials are bogus, so it must fail cleanly and still close the session.)

- [ ] **Step 6: Commit**

```bash
git add scripts/dev/ISS57_test_main_entrance_open.py
git commit -m "feat(scripts): add ISS-57 main-entrance open test script"
```

---

### Task 2: Write the ZIP README

**Files:**
- Create: `scripts/dev/ISS57_README.md`

- [ ] **Step 1: Write the README**

Write `scripts/dev/ISS57_README.md` with exactly this content:

```markdown
# Issue #57 — Main-Entrance Open Test

Thanks for helping debug this! This little script checks whether your main
entrance can be opened by commanding the **bridge** of your BTicino system —
which is how the official Netatmo app does it.

## ⚠️ Safety first

Running the **unlock** command can **physically open a real door**. Only run
this when you are at home and it is safe for the door to open. The script asks
you to type `YES` before every command.

## Prerequisites

- Python 3.11 or newer (`python3 --version`)
- Install the library: `pip install pybticino`

## Step 1 — Send us your Home Assistant diagnostics

In Home Assistant:

1. Settings → Devices & services
2. Click **BTicino Intercom**
3. Click the ⋮ (three-dots) menu → **Download diagnostics**
4. Attach the downloaded JSON file to the GitHub issue.

This lets us cross-check which modules your system exposes. (The file is
redacted by the integration — it does not contain your password.)

## Step 2 — Run the test script

```
python3 ISS57_test_main_entrance_open.py
```

- Enter your BTicino/Netatmo (Home + Security) **email** and **password**
  when prompted. The password is not shown as you type and is not saved.
- The script prints the list of modules in your home.
- Use the menu:
  - `u` → send **unlock / open** (`{"lock": false}`)
  - `l` → send **lock / close** (`{"lock": true}`)
  - `q` → quit
- Type `YES` to confirm each send.

## Step 3 — Tell us what happened

- Did pressing `u` **physically open** your main entrance?
- Did `l` do anything noticeable?
- Paste the script's output into the issue (it does not contain your password).

> We don't yet know how your particular entrance is wired — whether it
> supports both lock and unlock, or only a momentary open. That's exactly what
> this test tells us. Most video intercoms simply *open* and assume the door
> re-closes on its own.
```

- [ ] **Step 2: Commit**

```bash
git add scripts/dev/ISS57_README.md
git commit -m "docs(scripts): add ISS-57 test README for the reporter"
```

---

### Task 3: Build the ZIP package

**Files:**
- Create (build artifact, git-ignored or attached manually): `dist/iss57-main-entrance-test.zip`

- [ ] **Step 1: Build the ZIP with a clean top-level folder**

The ZIP must contain a folder `iss57-main-entrance-test/` holding the script
(renamed without the `ISS57_` README prefix collision) and `README.md`.

Run:
```bash
rm -rf /tmp/iss57-main-entrance-test && mkdir -p /tmp/iss57-main-entrance-test
cp scripts/dev/ISS57_test_main_entrance_open.py /tmp/iss57-main-entrance-test/ISS57_test_main_entrance_open.py
cp scripts/dev/ISS57_README.md /tmp/iss57-main-entrance-test/README.md
mkdir -p dist
(cd /tmp && zip -r - iss57-main-entrance-test) > dist/iss57-main-entrance-test.zip
unzip -l dist/iss57-main-entrance-test.zip
```
Expected: the listing shows `iss57-main-entrance-test/README.md` and
`iss57-main-entrance-test/ISS57_test_main_entrance_open.py`.

- [ ] **Step 2: Confirm dist is not committed accidentally**

Run: `git check-ignore dist/iss57-main-entrance-test.zip || echo "NOT IGNORED"`
If it prints `NOT IGNORED`, do **not** `git add` the zip — it is an
attachment artifact, not source. Leave it untracked. (No commit in this task.)

---

### Task 4: Draft the issue #57 reply

**Files:**
- Create: `scripts/dev/ISS57_issue_reply.md` (draft for the user to post; not the final source of truth)

- [ ] **Step 1: Write the reply draft (English)**

Write `scripts/dev/ISS57_issue_reply.md` with exactly this content:

```markdown
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
```

- [ ] **Step 2: Commit**

```bash
git add scripts/dev/ISS57_issue_reply.md
git commit -m "docs(scripts): add ISS-57 issue reply draft"
```

- [ ] **Step 3: Hand off to the user**

Tell the user the ZIP is at `dist/iss57-main-entrance-test.zip` and the reply
draft is at `scripts/dev/ISS57_issue_reply.md`. Do NOT post to GitHub or send
anything externally without explicit approval (per the no-prod / external-action
rules).
```

---

## Self-Review

**Spec coverage:**
- Standalone interactive script (bridge target, both payloads, loop, safety gate) → Task 1 ✓
- ZIP package (folder + script + README) → Tasks 2 & 3 ✓
- English issue reply with lock-vs-unlock note → Task 4 ✓
- Verification (parse, ruff, bad-cred dry run) → Task 1 steps 3-5 ✓

**Placeholder scan:** No TBD/TODO; all code and commands are complete.

**Type consistency:** Uses real `pybticino` surface — `AuthHandler(email, password)`, `AsyncAccount(auth)`, `account.async_update_topology()`, `account.homes` (dict), `Home.modules`, `Module.id/.name/.type`, `Home.raw_data["timezone"]`, `account.async_set_module_state(home_id, module_id, state, timezone, bridge_id)`, `auth.close_session()`. Bridge detected via `type in {"BNC1","BNCX"}`. Consistent across tasks.

**Note:** `Module` has no `reachable` attribute on the dataclass; the inventory prints name/type/id/flags only (no reachable column), matching the model. This is intentional — topology data doesn't carry live reachability.
