Hi @sorighepinnadu,

Update, and a different direction.

First, let's drop `vde.Unlock` for good — that command is only used by the app for the
newer BCEPL "Connected Entrance Panel" product, which your setup doesn't have, so the
403 is expected and not something we can fix. Done with that.

More importantly: I can now rule out the "it only works during a call" idea. I traced
the app's open-door action all the way down, and for a BNDL lock the app sends exactly
what Home Assistant already sends — a `setstate` with `{"lock": false}` on the lock
module, with the bridge, to `/syncapi/v1/setstate`. Same module, same bridge, same body
(HA actually sends a bit more). And I've confirmed on identical hardware (a Classe 100X
with two external units and three BNDL locks, same as yours) that this exact command
opens the door cold, with no call, reliably. So the command is correct and a call is not
required. The problem is specific to your installation, not to the protocol.

That leaves two concrete things to check on your side:

**1. Which lock is actually the street door.** You have three BNDL modules. Only one of
them is wired to the main entrance; the others are likely a secondary door or unused.
HA exposes all three as separate lock entities, and the cloud happily returns `ok` for a
`setstate` to any valid BNDL whether or not it physically opens anything. So if the lock
you've been triggering isn't the one wired to the street door, you'd see exactly what
you're seeing: `ok`, nothing moves.

Could you trigger each of the three locks one at a time — from HA, or the script — and
note which physical door or gate (if any) responds to each? That tells us which module is
the real entrance.

**2. Reachability.** The official app won't even show the unlock control when the bridge
is in an error/unreachable state — it just hides it. HA, on the other hand, sends the
command regardless and reports an optimistic success. So if your BNC1 bridge or the
target lock is degraded/offline at the moment, the cloud returns `ok` but never delivers
the command to the device.

When you trigger the locks, can you also check whether each BNDL (and the bridge) shows
as reachable/online in HA — and grab the diagnostics around that moment? If the entrance
lock is showing unreachable, that's our answer, and I'll make HA surface that instead of
a fake success.

One more: does your main entrance even have its own lock relay module, or does the
street door share the external unit? If it's the latter, the "lock" we're targeting may
not be the thing that opens it at all.

Thanks again for the patience — this narrows it down a lot.
