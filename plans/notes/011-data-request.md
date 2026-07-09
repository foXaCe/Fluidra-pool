# Data request — per-component ACL on shared pools (draft for issue #133)

> Draft, ready to paste as a comment on
> https://github.com/foXaCe/Fluidra-pool/issues/133. Not posted by this
> spike — posting is a maintainer action.

---

We now know (thanks to @Kal42's owner-side dump) that a **viewer** contract
gets a fake HTTP 200 that never persists, and the integration blocks writes
for that confirmed case since v2.48.0. What's still missing is data on any
**non-viewer, non-owner** access level, and a direct (not inferred) capture
of the per-component 404-vs-200 split first reported in this issue.

If you have a Fluidra pool that is **shared with another account**, this is
where your report would help most — especially if you can get **both**
sides of the same pool (the owner *and* the person it's shared with).

## What we need

### (a) A diagnostics dump from each account

From **each** Home Assistant instance involved (the owner's and the
shared-with account's, if you can get both):

1. Home Assistant → Settings → Devices & Services → Fluidra Pool → the
   three-dot menu → **Download diagnostics**.
2. Attach the file (or paste the relevant `contracts` block) as a comment
   here.

The diagnostics file already redacts serials and emails automatically — you
do not need to hand-edit it before posting. If you'd rather double-check
before posting, the field we actually need is the pool's `contracts` list —
specifically each entry's `accessLevel` value (e.g. `"viewer"`, or whatever
else your account shows).

### (b) For the non-owner account: three targeted write attempts in the official Fluidra app

With the **non-owner** account, in the official Fluidra mobile app (not
Home Assistant — we want to see the cloud's raw behavior, not the
integration's), please try these three writes on the same device and note
what happens for each:

1. **A setpoint** (e.g. heat pump target temperature, or pH/ORP setpoint on
   a chlorinator) — change it to a new value.
2. **A boost** (e.g. chlorinator boost, or a heat pump boost/smart preset if
   your device has one) — turn it on.
3. **An on/off toggle** (e.g. pump start/stop, heat pump on/off) — flip it.

For each one:
- Did the app show the change as accepted (no error)?
- **Did the value actually hold after waiting about 1 minute**, then
  refreshing / reopening the app? (This is the key question — a value that
  reverts or never moved is the "fake success" behavior we're trying to
  map.)

If you're comfortable pulling the raw HTTP status (e.g. via a browser
network inspector on the Fluidra web app if you use it, or any proxy/logging
you already have set up) that's a bonus, but not required — the app's
visible behavior (accepted vs error message, held vs reverted) is enough to
make progress.

### What to redact before posting

Diagnostics dumps already redact serials and emails automatically — no
manual redaction should be needed. If you paste raw JSON instead of
attaching the file, please double-check there's no email, serial number, or
access token in what you paste.

## Response format

A filled-in table like this (one row per write attempt) is the easiest for
us to use — please copy it into your comment and fill in what you have:

| Account role | Device / component | Write attempted | Accepted by app (Y/N) | Value held after ~1 min (Y/N) | `accessLevel` from diagnostics (if known) |
|---|---|---|---|---|---|
| owner | | | | | |
| shared (non-owner) | chlorinator pH setpoint | changed setpoint | | | |
| shared (non-owner) | chlorinator boost | turned on | | | |
| shared (non-owner) | pump on/off | turned off | | | |

Even a partial table (just the accessLevel from a diagnostics dump, or just
one of the three write attempts) is useful — you don't need to complete
every row to help.

Thank you — this is exactly the kind of report that unblocked the
`viewer`-only guard in v2.48.0, and the same pattern (a real capture from
someone on a shared pool) is what would let us extend it safely without
risking false positives for owners.
