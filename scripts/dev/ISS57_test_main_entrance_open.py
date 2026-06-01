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
