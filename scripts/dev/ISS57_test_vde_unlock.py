#!/usr/bin/env python3
"""Issue #57 — test the REAL "open entrance" command (vde.Unlock).

Background: the earlier test (ISS57_test_main_entrance_open.py) proved that
`setstate {"lock": ...}` is a physical no-op on this hardware. By reverse
engineering the official Home + Security app we found the command it actually
sends to open the door. It is NOT setstate. It is a JSON-RPC method called
`vde.Unlock`, delivered through a dedicated cloud endpoint:

    POST https://app.netatmo.net/api/v1/sendSyncCommand

The body is a normal "home" envelope, but the target module carries two extra
keys: a base64-encoded JSON-RPC `payload`, and `configurationPhase: false`:

    {
      "home": {
        "id": "<home_id>",
        "modules": [{
          "id": "<module_id>",
          "bridge": "<bridge_id>",
          "payload": "<base64 of the JSON-RPC below>",
          "configurationPhase": false
        }]
      }
    }

The base64 `payload` decodes to:

    {"id": "<uuid>", "jsonrpc": "2.0",
     "method": "vde.Unlock", "params": {"secondaryLock": <bool>}}

  - secondaryLock = false  -> PRIMARY lock   (main entrance / door release)
  - secondaryLock = true   -> SECONDARY lock (gate)

This helper authenticates, lists your modules, lets you pick a target module
and which lock to open, then sends exactly that request and prints the full
HTTP + JSON-RPC response.

SAFETY: this can physically open a real door. Run it only when present and
safe. Each send asks you to type YES first (unless --yes is given).

Usage:
    pip install pybticino aiohttp

    # interactive (recommended first time):
    python3 ISS57_test_vde_unlock.py

    # just list modules and their ids/types, then exit:
    python3 ISS57_test_vde_unlock.py --list

    # one-shot, open the PRIMARY lock of a specific module:
    python3 ISS57_test_vde_unlock.py --module-id <id>

    # one-shot, open the SECONDARY lock (gate):
    python3 ISS57_test_vde_unlock.py --module-id <id> --secondary

No credentials are stored; you are prompted and the password is never echoed.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import getpass
import json
import logging
import sys
import uuid

import aiohttp
from pybticino import AsyncAccount, AuthError, AuthHandler
from pybticino.const import BASE_URL, DEFAULT_APP_VERSION, build_user_agent

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
_LOGGER = logging.getLogger("iss57-vde")

SYNC_COMMAND_URL = f"{BASE_URL}/api/v1/sendSyncCommand"
BRIDGE_TYPES = {"BNC1", "BNCX"}
EXTERNAL_UNIT_TYPE = "BNEU"
UNLOCK_METHOD = "vde.Unlock"
# The official app tags every API call with its vertical (app_type) and version.
# Omitting these made the backend reject vde.Unlock with HTTP 403 code 13
# "Device does not belong to the home" — it could not resolve the device under
# an unknown app vertical. pybticino's working calls always send app_type=app_camera.
APP_TYPE = "app_camera"


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


def _print_inventory(home, bridge_id: str | None) -> None:
    """Print every module in the home, flagging bridge and external units."""
    print(f"\nModules in home '{home.name}' (id={home.id}):")
    print(f"  {'idx':<4} {'name':<24} {'type':<6} {'id':<30} flags")
    for i, module in enumerate(home.modules):
        flags = []
        if module.id == bridge_id:
            flags.append("BRIDGE")
        if module.type == EXTERNAL_UNIT_TYPE:
            flags.append("EXTERNAL_UNIT")
        name = module.name or ""
        print(f"  {i:<4} {name:<24} {module.type:<6} {module.id:<30} {','.join(flags)}")
    print()


def _build_payload(secondary_lock: bool) -> str:
    """Return the base64 of the JSON-RPC vde.Unlock request."""
    jsonrpc = {
        "id": str(uuid.uuid4()),
        "jsonrpc": "2.0",
        "method": UNLOCK_METHOD,
        "params": {"secondaryLock": secondary_lock},
    }
    raw = json.dumps(jsonrpc).encode("utf-8")
    return base64.b64encode(raw).decode("ascii")


async def _send_unlock(
    token: str,
    home_id: str,
    module_id: str,
    bridge_id: str | None,
    secondary_lock: bool,
) -> None:
    """POST a single vde.Unlock sync command and print the full response."""
    payload_b64 = _build_payload(secondary_lock)
    module_body: dict = {"id": module_id, "payload": payload_b64, "configurationPhase": False}
    if bridge_id:
        module_body["bridge"] = bridge_id
    # app_type / app_version sit as siblings of "home", exactly like every other
    # Netatmo API call (the app appends them to all requests; pybticino does too).
    body = {
        "app_type": APP_TYPE,
        "app_version": DEFAULT_APP_VERSION,
        "home": {"id": home_id, "modules": [module_body]},
    }

    lock_name = "SECONDARY (gate)" if secondary_lock else "PRIMARY (main entrance)"
    print("\n--- request ---")
    print(f"POST {SYNC_COMMAND_URL}")
    print(f"target module : {module_id}")
    print(f"bridge        : {bridge_id}")
    print(f"lock          : {lock_name}  (secondaryLock={secondary_lock})")
    print(f"decoded payload: {base64.b64decode(payload_b64).decode('utf-8')}")
    print(f"body          : {json.dumps(body)}")

    headers = {
        "Authorization": f"Bearer {token}",
        "User-Agent": build_user_agent(),
        "Content-Type": "application/json; charset=utf-8",
    }
    async with aiohttp.ClientSession() as session:
        async with session.post(SYNC_COMMAND_URL, json=body, headers=headers, timeout=30) as resp:
            text = await resp.text()
            print("\n--- response ---")
            print(f"HTTP {resp.status}")
            try:
                parsed = json.loads(text)
                print(json.dumps(parsed, indent=2))
            except ValueError:
                print(text)
    print()


def _confirm(skip: bool) -> bool:
    """Return True if the user typed YES (or the gate is skipped)."""
    if skip:
        return True
    return input("Type YES to send (anything else cancels): ").strip() == "YES"


async def main() -> None:
    """Run the interactive / one-shot vde.Unlock tester."""
    parser = argparse.ArgumentParser(description="ISS-57 vde.Unlock entrance tester")
    parser.add_argument("--list", action="store_true", help="List modules and exit.")
    parser.add_argument("--module-id", help="Target module id (one-shot mode).")
    parser.add_argument(
        "--secondary",
        action="store_true",
        help="Open the SECONDARY lock (gate). Default opens the PRIMARY lock.",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Skip the typed-YES safety gate (for repeated CLI testing).",
    )
    args = parser.parse_args()

    email = input("BTicino/Netatmo email: ").strip()
    password = getpass.getpass("Password: ")

    auth = AuthHandler(email, password)
    account = AsyncAccount(auth)
    try:
        print("Authenticating + fetching topology...")
        token = await auth.get_access_token()
        await account.async_update_topology()

        home = _select_home(account)
        bridge_id = _find_bridge_id(home)
        _print_inventory(home, bridge_id)

        # one-shot mode
        if args.list:
            return
        if args.module_id:
            print(
                f"About to send vde.Unlock to {args.module_id} "
                f"(secondaryLock={args.secondary})."
            )
            if _confirm(args.yes):
                await _send_unlock(
                    token, home.id, args.module_id, bridge_id, args.secondary
                )
            else:
                print("Cancelled.\n")
            return

        # interactive mode
        modules = list(home.modules)
        while True:
            print(
                "Menu:  [s] send vde.Unlock to a module   "
                "[L] list modules   [q] quit"
            )
            choice = input("> ").strip().lower()
            if choice == "q":
                break
            if choice == "l":
                _print_inventory(home, bridge_id)
                continue
            if choice != "s":
                print("Unknown choice.")
                continue

            idx = input("Target module index (see list): ").strip()
            if not idx.isdigit() or not (0 <= int(idx) < len(modules)):
                print("Invalid index.")
                continue
            target = modules[int(idx)]

            lock = input(
                "Which lock?  [p] primary / main entrance   [s] secondary / gate: "
            ).strip().lower()
            if lock not in ("p", "s"):
                print("Invalid lock choice.")
                continue
            secondary = lock == "s"

            print(
                f"\nAbout to send vde.Unlock to module {target.id} "
                f"(type={target.type}, secondaryLock={secondary})."
            )
            if _confirm(args.yes):
                await _send_unlock(token, home.id, target.id, bridge_id, secondary)
            else:
                print("Cancelled.\n")
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
