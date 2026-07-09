# 011 — Per-component ACL analysis for shared pools

> Spike from plan `011-spike-per-component-acl.md`. Analysis document only —
> no `custom_components/` code is touched by this work. Code reference:
> commit `a013d54` (verified — `git diff --stat a013d54..HEAD -- helpers.py
> entity.py __init__.py` is empty at the time of writing).

## 1. Matrix of current code paths

`determine_pool_access(pool, user_id)` (`custom_components/fluidra_pool/helpers.py:70-103`)
returns one of four known values, or an arbitrary unrecognized cloud string.
Guard behavior for each:

| Return value of `determine_pool_access` | Return condition | Guard behavior (`_ensure_pool_writable` / `_ensure_device_pool_writable`) | Residual risk |
|---|---|---|---|
| `"owner"` | `pool.owner == user_id` | Passes — write allowed | None (confirmed case, the owner always has real control) |
| `"viewer"` | every `contracts[]` entry has `accessLevel == "viewer"`, or the current user's own contract has `accessLevel == "viewer"` | **Blocked** — `ServiceValidationError(pool_read_only)` raised before any optimistic state | None — the only case confirmed by capture (`@Kal42`'s owner dump, 2026-07-08) |
| `"shared"` | `contracts[]` is non-empty but mixed (not all `"viewer"`), and no contract matches `user_id` | Passes — write allowed | **Residual**: this case assumes at least one non-viewer level exists in the mix, but none has ever been observed. If the cloud returns a mix that includes an unrecognized read-only level (e.g. `"guest"`), the user passes the guard when they shouldn't. |
| `"unknown"` | `contracts` missing, not a list, or empty | Passes — write allowed | Low in practice (a pool with no `contracts[]` is almost always a non-shared pool where the user is de facto owner), but remains an unproven fail-open behavior. |
| **unrecognized cloud string, passed through as-is** (e.g. `"editor"`, `"admin"`, `"guest"`) | the current user's contract has an `accessLevel` that is a string but not `"viewer"` — returned as-is (line 99: `return level`) | Passes — write allowed, since only the `== "viewer"` comparison blocks (`entity.py:118`, `__init__.py:329`) | **This is the key point of the spike.** If Fluidra uses an intermediate level such as `"editor"` that does not actually have full write rights (or has partial, per-component write rights — see section 2), a user with that level passes the guard entirely today and can reproduce the original #129/#133 bug: commands accepted with a 200-echo but never persisted, or 404 depending on the component. |

**Matrix conclusion**: the current guard is a binary filter (`viewer`
blocks, everything else passes) built on a single confirmed capture
(`@Kal42`'s owner dump, which shows only `"viewer"` contracts). The choice
to block only `"viewer"` is documented as deliberately conservative in
`entity.py:116` ("Only the confirmed-read-only level blocks; owner/shared/
unknown pass through.") — that is the lowest-risk choice given the current
data, but it leaves an open window for any intermediate access level not
yet observed.

## 2. Per-component evidence

### Original evidence recap (issue #133, reported by @luistf76 in #129)

```
Control component 8 SUCCESS: HTTP 200, desiredValue=730,
response={"id":8,"reportedValue":730,"desiredValue":730,"ts":...}
```
> "pH/ORP setpoints silently revert; boost returned HTTP 404."

Cross-referencing with the repo's code — component 8 maps precisely to
`ph_setpoint` (write) for at least two chlorinator families:

```
custom_components/fluidra_pool/device_registry/configs/chlorinators.py:87:
    "ph_setpoint": {"write": 8, "read": 172},  # Component 8 (write) / 172 (read).
custom_components/fluidra_pool/device_registry/configs/chlorinators.py:728:
    "ph_setpoint": 8,  # 740 = 7.40 pH.
```

The "boost" that returned 404 is most likely the chlorinator's boost
component (`switch/chlorinator.py`), whose default component id is 245
(`DeviceIdentifier.get_feature(self.device_data, "boost_mode", 245)`,
`switch/chlorinator.py:74,92,139,172`). This is **not proven** by the
original capture (it does not explicitly show the boost's component
number), but it is the only "boost" write in the repo for the chlorinator
family, consistent with the "boost" vocabulary used in the issue.

### Repo write inventory, by family

Search: `grep -rn "control_device_component\|set_component_value\|set_component_string_value\|set_component_json_value\|set_schedule\|clear_schedule" custom_components/fluidra_pool/fluidra_api/_components.py custom_components/fluidra_pool/fluidra_api/_schedules.py custom_components/fluidra_pool/fluidra_api/_commands.py`
(note: the plan's suggested grep, `send_command|set_component` against
`_commands.py`, returns nothing — that file only holds high-level methods
like `start_pump`/`stop_pump`/`enable_auto_mode` that delegate to
`control_device_component`, which is defined in `_components.py`, not
`_commands.py`. Naming mismatch documented here; the inventory below is
built from the real write entry point instead:
`ComponentsMixin.control_device_component` in `fluidra_api/_components.py:40`,
plus the `set_component_value` / `set_component_string_value` /
`set_component_json_value` wrappers and `SchedulesMixin.set_schedule` /
`clear_schedule` in `_schedules.py`).

| Family | Component(s) written | Call site | Known viewer behavior? |
|---|---|---|---|
| Pump (VS*/VT*/NCC*) on/off | `COMPONENT_PUMP_ONOFF` (9) | `fluidra_api/_commands.py:48,61` (`start_pump`/`stop_pump`), `switch/pump.py` | No |
| Pump auto mode | `COMPONENT_AUTO_MODE` (10) | `fluidra_api/_commands.py:75,79` | No |
| Pump speed | `COMPONENT_PUMP_SPEED` (11) | `fluidra_api/_commands.py:52`, `select/pump.py` | No |
| Heat pump on/off | `COMPONENT_HEAT_PUMP_ONOFF` (13) | `fluidra_api/_commands.py:46,60`, `climate.py:483,509` (Z260iQ) | No |
| Heat pump mode/preset (LG, Z260iQ — likely includes the "boost" wording from #129 if the original reporter had a heat pump rather than a chlorinator) | component 14 | `climate.py:474 → 21/0; 463 → 21/1; 507,637` | No — but this is the closest family to the "boost 404" narrative if the original report came from a heat pump rather than a chlorinator (ambiguous, see above) |
| Heat pump setpoint | `COMPONENT_HEAT_PUMP_SETPOINT` (15) | `fluidra_api/_commands.py:28` (`set_heat_pump_temperature`) | No |
| Z550iQ on/off | component 21 | `climate.py:460,463,479` | No |
| Z550iQ mode | component 16 | `climate.py:474` | No |
| Chlorinator pH setpoint | component 8 (write) on ≥2 families, otherwise 16/40/157 depending on device | `number.py:153,238,335` (generic, via `resolve_component_rw`) | **Yes — 200-echo, not persisted**, original @luistf76 capture (component 8 confirmed by the captured payload) |
| Chlorinator ORP setpoint | component 11 (write) on ≥2 families, otherwise 20/39 depending on device | `number.py` (generic) | Not directly captured, but same component family as the pH setpoint (contiguous numbering 8/11) — likely the same behavior |
| Chlorinator boost | `boost_mode` component, default 245 | `switch/chlorinator.py:74,92,111,139,144,172` | **Suspected — HTTP 404**, but not confirmed by an explicit component number in the original capture |
| Chlorinator mode | `mode_comp` (device-dependent) | `switch/chlorinator.py:108`, `select/chlorinator.py:115` | No |
| Chlorinator on/off | `on_off_component` (device-dependent) | `switch/chlorinator.py:231,262` | No |
| Light brightness | `COMPONENT_LIGHT_BRIGHTNESS` (17) | `light.py`, `select/light.py`, `time/light.py` | No |
| Light color (RGBW) | `COMPONENT_LIGHT_COLOR` (45) | `light.py` (via `set_component_json_value`) | No |
| Schedule (standard CRON format) | `COMPONENT_SCHEDULE` (20) | `fluidra_api/_schedules.py:102` (`set_schedule`), `time/schedule.py`, `select/schedule.py`, `switch/schedule.py`, `number.py:405` | No |
| Schedule (DM24049704 format, programs/slots) | `COMPONENT_DM24049704_SCHEDULE` (258) | `fluidra_api/_schedules.py:117` | No |

**"Known viewer behavior" column — summary**: out of ~16 write families
inventoried, **only 1** (chlorinator pH setpoint, component 8) has a viewer
behavior confirmed by an exact capture (200-echo, not persisted). The
"boost 404" from the original quote is strongly suspected to be the
chlorinator's boost component (default 245), but that link is **not**
proven by an explicit component number in the capture — only the next data
collection round (`011-data-request.md`) can confirm it.

### What the available evidence can and cannot say

- The 404 (boost) vs 200-echo (setpoint) differential on the same viewer
  account, for the same device (a chlorinator), is the only direct evidence
  of a **per-component** control on the cloud side, rather than a simple
  per-pool binary flag.
- This evidence comes from a single report (issue #129/#133, @luistf76), on
  a single device family (chlorinator). It says nothing about pumps, heat
  pumps, or lights.
- There is no capture confirming that any `accessLevel` other than
  `"viewer"` (e.g. `"editor"`) behaves differently from the owner on
  writes — that is the maintainer's open question #1 in #133.

## 3. Candidate design (pre-decision, to be validated by data)

Two non-exclusive approaches are worth considering once data is available.
Neither is implemented by this spike.

### Option A — Static capability-map `{component_id: write_allowed}` per access level

A lookup table (e.g. `ACCESS_LEVEL_COMPONENT_ACL`) listing, for each known
cloud `accessLevel`, which `component_id`s the cloud actually honors writes
on (as opposed to those that return 404 or an unpersisted 200-echo).

**Advantages**:
- Immediate detection, before the HTTP request is even sent — no wasted
  round-trip, no optimistic state to roll back.
- Precise, actionable error message (e.g. "this component isn't
  controllable from this access level", similar to today's
  `pool_read_only`).

**Disadvantages**:
- Requires maintaining a table keyed by `(accessLevel, component_id)` — the
  component set is already large (16+ families inventoried above) and
  varies per device family (component ids differ across chlorinator models,
  see the `chlorinators.py` table).
- Risk of **over-blocking**: any `(accessLevel, component)` combination not
  in the table needs a default behavior — either "block by caution" (risk
  of blocking legitimate owners on devices not yet cataloged), or "allow"
  (today's status quo, so no progress until the table is exhaustive).
- Fragile against changes on Fluidra's undocumented cloud API — the table
  can silently go stale.

### Option B — On-the-fly "unpersisted 200-echo" detection

After a `control_device_component` call that returns HTTP 200, compare the
`reportedValue` returned immediately (which, per the component-8 capture,
always fails to reflect the real value for a viewer) against the state
observed at the **next coordinator poll**. If the value has not converged
toward `desiredValue` after a reasonable delay (e.g. 1-2 polling cycles),
raise an error after the fact, mark the entity as degraded, or log a
warning.

**Advantages**:
- No per-component table to maintain — works for any `accessLevel` or
  component, including ones never cataloged.
- Relies on the actually observed cloud behavior (200-echo) rather than an
  a-priori classification that can be wrong.
- Trivially covers the 404 case too (that call already fails today —
  `control_device_component` returns `False`).

**Disadvantages**:
- **Late detection**: the command has already been sent, the optimistic
  state already shown (at least briefly) before being invalidated — worse
  UX than blocking upfront.
- Needs a "pending confirmation" state per component and a time tolerance
  window (false positives are possible if a device legitimately takes
  longer to converge, e.g. a chlorinator setpoint with physical inertia —
  distinct from the viewer case).
- Adds complexity to the coordinator (desired-vs-reported comparison across
  multiple cycles, per component, with a tolerance budget) for a benefit
  that is marginal today (the one confirmed case, `viewer`, is already
  blocked upfront — this detection would only help the still-unidentified
  intermediate levels).

### Spike recommendation (pending maintainer confirmation)

Neither option should start before at least one capture of a non-viewer
`accessLevel` with its real write behavior is available (see section 4). If
the data shows the cloud does have an intermediate level with per-component
rights, Option B (on-the-fly detection) is probably more robust long-term
since it does not depend on knowing every `(accessLevel, component_id)`
pair in advance — but Option A remains preferable if the data shows a
simple, stable model (e.g. a single intermediate level with a short, fixed
component list). A hybrid is possible: Option A for cases confirmed by
capture (like `viewer` today), with Option B as a safety net for
uncataloged cases.

## 4. "Ready to implement" checklist

Implementation (whether Option A, B, or a hybrid) should only start once
all three of the following are true:

- [ ] At least one non-viewer `accessLevel` value (e.g. `"editor"`,
      `"admin"`) is confirmed by a real capture (diagnostics dump + observed
      write behavior), with its real write behavior documented (allowed /
      denied, and on which components).
- [ ] The 404 (hard rejection) / 200-echo (accepted but not persisted) pair
      is confirmed on **at least 2 components** by a direct capture (not an
      inference like the one made in section 2 for "boost" — that one needs
      to be replaced with direct proof).
- [ ] The maintainer has decided the over-blocking (Option A) vs late
      detection (Option B) trade-off documented in section 3.

Until these three conditions are met, the status quo (block only the
confirmed `"viewer"` level, pass everything else) remains the lowest-risk
choice: it never blocks a legitimate owner, and it is the only behavior
fully backed by a real capture to date.
