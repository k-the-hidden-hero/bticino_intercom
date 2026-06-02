# ISS-57 — `vde.Unlock` entrance test

## What we found

Your earlier test results were decisive: sending `setstate {"lock": false/true}`
to the bridge / BNDL modules returns `{"status": "ok"}` but **never opens the
entrance**, and the BNEU rejects it (`code 5`). So the official app does **not**
use `setstate` at all to open the door.

By reverse engineering the Home + Security app we found the real command. It is
a **JSON-RPC method `vde.Unlock`** sent to a dedicated cloud endpoint:

```
POST https://app.netatmo.net/api/v1/sendSyncCommand
```

The request is a normal "home" envelope, but the target module carries a
base64-encoded JSON-RPC `payload` plus `configurationPhase: false`:

```json
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
```

The base64 `payload` decodes to:

```json
{"id": "<uuid>", "jsonrpc": "2.0",
 "method": "vde.Unlock", "params": {"secondaryLock": false}}
```

- `secondaryLock: false` → **PRIMARY** lock (main entrance / door release)
- `secondaryLock: true`  → **SECONDARY** lock (gate)

The script in this archive builds and sends exactly that request.

## Install

```
pip install pybticino aiohttp
```

## Run

> ⚠️ This can physically open a real door. Run only when you are present and it
> is safe. Each send asks you to type `YES` first (unless you pass `--yes`).

**1) List your modules** (to get the exact ids/types):

```
python3 ISS57_test_vde_unlock.py --list
```

**2) Interactive mode** (recommended): pick a module and the lock from a menu:

```
python3 ISS57_test_vde_unlock.py
```

**3) One-shot, by module id.** The most likely target is one of your **BNEU
(external unit)** modules. Try the primary lock first, then the secondary:

```
# PRIMARY lock (main entrance) of an external unit
python3 ISS57_test_vde_unlock.py --module-id <BNEU_module_id>

# SECONDARY lock (gate) of the same external unit
python3 ISS57_test_vde_unlock.py --module-id <BNEU_module_id> --secondary
```

You have two BNEU units, so the full matrix to try is:

```
python3 ISS57_test_vde_unlock.py --module-id <BNEU_1>
python3 ISS57_test_vde_unlock.py --module-id <BNEU_1> --secondary
python3 ISS57_test_vde_unlock.py --module-id <BNEU_2>
python3 ISS57_test_vde_unlock.py --module-id <BNEU_2> --secondary
```

If none of the BNEU targets work, it is also worth trying the bridge (BNC1) id
and the BNDL ids with this command — the transport here is completely different
from the old `setstate` test, so a module that ignored `setstate` may still
accept `vde.Unlock`.

## What to report back

For each command you run, please paste:

- the **module id + type** you targeted and whether it was primary/secondary,
- the **HTTP status** and the **full JSON response** the script prints,
- and, most importantly, **whether the door/gate physically opened**.

That tells us the exact (module, secondaryLock) combination to wire into the
integration as a proper "Open Main Entrance" control.
