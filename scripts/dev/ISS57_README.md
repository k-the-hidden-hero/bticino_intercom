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
