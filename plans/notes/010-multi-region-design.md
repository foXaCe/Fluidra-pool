# Plan 010 (spike) — Multi-region design notes

Status: design-only. No code in this plan. Blocked on a traffic capture from a
non-EMEA (APAC or North America) account — see issue #91.

## 0. Inventory of regional wiring (Step 1)

Verified commands (run from the repo root, 2026-07-09, against commit
`a013d54` / worktree branch `advisor/010-spike-multi-region`):

```
grep -rn "fluidra-emea\|cognito-idp\|amazonaws" custom_components/fluidra_pool/ --include="*.py"
grep -rn "emea\|eu-west" custom_components/ -i --include="*.py"
```

Result: the **only** literal hardcoded regional strings in the whole
integration are the two lines below. Everywhere else, files *import* the
constants — they don't redeclare URLs. No STOP condition triggered (the plan
would have flagged hardcoded regional URLs found outside `_constants.py`;
none were found — every other file goes through the constant, confirmed by
grep and by manual read of each hit).

### 0.1 Source of truth — `custom_components/fluidra_pool/fluidra_api/_constants.py`

| Line | Symbol | Value | Regional? |
|---|---|---|---|
| 7 | `FLUIDRA_EMEA_BASE` | `https://api.fluidra-emea.com` | **Yes** — the data-plane host |
| 8 | `COGNITO_ENDPOINT` | `https://cognito-idp.eu-west-1.amazonaws.com/` | **Yes** — AWS Cognito IdP region |
| 9 | `COGNITO_CLIENT_ID` | `g3njunelkcbtefosqm9bdhhq1` | **Yes** — Cognito app-client id is per user-pool, hence per region. Public identifier (sent in cleartext in every `InitiateAuth` body), not a secret. |
| 12 | `USER_POOLS_ENDPOINT` | `f"{FLUIDRA_EMEA_BASE}/generic/users/me/pools"` | Derived — regional only via the base |
| 13 | `DEVICES_ENDPOINT` | `f"{FLUIDRA_EMEA_BASE}/generic/devices"` | Derived |
| 14 | `CONSUMER_PROFILE_ENDPOINT` | `f"{FLUIDRA_EMEA_BASE}/mobile/consumers/me"` | Derived |
| 16 | `CONNECTED_PARAMS` | `{"deviceType": "connected"}` | **Unknown** — see 0.3 |
| 17-20 | `FLUIDRA_USER_AGENT` | Android app UA string (`com.fluidra.iaqualinkplus/…`) | **Unknown** — see 0.3 |
| 21 | `RETRYABLE_STATUSES` | `{429, 500, 502, 503, 504}` | No — generic HTTP transport behavior |
| 22 | `MAX_REFRESH_ATTEMPTS` | `1` | No — client-side retry policy |

### 0.2 Consumers (import the constants above — no duplicate literals)

| File:line | Symbol used | Role |
|---|---|---|
| `_auth.py:22-25` (import) | `COGNITO_CLIENT_ID`, `COGNITO_ENDPOINT`, `CONSUMER_PROFILE_ENDPOINT`, `FLUIDRA_USER_AGENT` | — |
| `_auth.py:71` | `COGNITO_CLIENT_ID` | `ClientId` field in `InitiateAuth` body (initial login) |
| `_auth.py:78` | `FLUIDRA_USER_AGENT` | `User-Agent` header, initial login |
| `_auth.py:83` | `COGNITO_ENDPOINT` | Request target, initial login |
| `_auth.py:110` | `COGNITO_CLIENT_ID` | `ClientId` field in `RespondToAuthChallenge` (MFA) |
| `_auth.py:121` | `FLUIDRA_USER_AGENT` | `User-Agent` header, MFA |
| `_auth.py:126` | `COGNITO_ENDPOINT` | Request target, MFA |
| `_auth.py:185` | `CONSUMER_PROFILE_ENDPOINT` | GET consumer profile (post-login) |
| `_auth.py:201` | `FLUIDRA_USER_AGENT` | `User-Agent` header, consumer profile call |
| `_auth.py:280` | `COGNITO_CLIENT_ID` | `ClientId` field in refresh-token flow |
| `_auth.py:287` | `FLUIDRA_USER_AGENT` | `User-Agent` header, refresh-token flow |
| `_auth.py:293` | `COGNITO_ENDPOINT` | Request target, refresh-token flow |
| `_devices.py:12` (import) | `DEVICES_ENDPOINT`, `FLUIDRA_EMEA_BASE`, `USER_POOLS_ENDPOINT` | — |
| `_devices.py:29` | `USER_POOLS_ENDPOINT` | GET — list user's pools |
| `_devices.py:50,53` | `DEVICES_ENDPOINT` + `params={"poolId":…, "format":"tree"}` | GET — list devices (tree shape), call site 1 |
| `_devices.py:206,209` | `DEVICES_ENDPOINT` + same params | GET — list devices (tree shape), call site 2 |
| `_devices.py:260` | `FLUIDRA_EMEA_BASE` (inline, **not** a named constant) | GET `/generic/pools/{pool_id}` |
| `_devices.py:263,266` | (same url) + `params={"pageSize": 1}` | GET — pool page probe |
| `_devices.py:286` | `FLUIDRA_EMEA_BASE` (inline) | GET `/generic/pools/{pool_id}` (second call site) |
| `_devices.py:294` | `FLUIDRA_EMEA_BASE` (inline) | GET `/generic/pools/{pool_id}/status` |
| `_components.py:13` (import) | `CONNECTED_PARAMS`, `FLUIDRA_EMEA_BASE` | — |
| `_components.py:27,28` | `FLUIDRA_EMEA_BASE` (inline) + `CONNECTED_PARAMS` | GET component, call site 1 |
| `_components.py:53,58` | same pattern | GET/PUT component, call site 2 |
| `_components.py:155,160` | same pattern | GET/PUT component, call site 3 |
| `_schedules.py:13` (import) | `CONNECTED_PARAMS`, `FLUIDRA_EMEA_BASE` | — |
| `_schedules.py:115,123` | `FLUIDRA_EMEA_BASE` (inline) + `CONNECTED_PARAMS` | PUT — schedule/component write |

Observation (informational, not a blocker for this spike): `_devices.py`,
`_components.py`, and `_schedules.py` build the `/generic/pools/...` and
`/generic/devices/{id}/components/{id}` URLs **inline** with
`f"{FLUIDRA_EMEA_BASE}/..."` rather than through a named `*_ENDPOINT`
constant like `USER_POOLS_ENDPOINT`. Functionally identical today (single
base), but a real multi-region implementation should decide whether to (a)
keep inlining against a per-instance `base_url`, or (b) introduce endpoint
*templates* in `_constants.py`/`RegionConfig` for consistency. Noted here so
the future implementer doesn't have to re-derive this list.

### 0.3 Non-evidences — honestly unknown without a capture

- **`FLUIDRA_USER_AGENT`**: this is the identifier of the *official Android
  app* (`com.fluidra.iaqualinkplus`), not obviously tied to a region — the
  same app binary is plausible across regions. But North America is branded
  "iAquaLink" in the maintainer's own words in issue #91 ("North America → a
  different backend (iAquaLink US)"), which raises the possibility of a
  **different app package / User-Agent per region**. Unknown until a
  volunteer captures it. If it differs, `FLUIDRA_USER_AGENT` must move into
  `RegionConfig` too (see §1).
- **`CONNECTED_PARAMS` (`deviceType=connected`) and `format=tree`**: these
  are query parameters on `/generic/devices` and `/generic/devices/{id}/components/{id}`.
  Nothing in the code or in issue #91 confirms whether other regional
  backends accept/require the same parameter names and values. Treated as
  universal *only as a starting assumption*, to be confirmed by the first
  capture.

## 1. Data model

```python
# custom_components/fluidra_pool/fluidra_api/_constants.py (future shape)

@dataclass(frozen=True)
class RegionConfig:
    """Per-region wiring for the Fluidra API + Cognito."""

    key: str                    # stable id stored in ConfigEntry.data, e.g. "emea"
    label: str                  # human-readable, for the config flow selector
    base_url: str                # e.g. "https://api.fluidra-emea.com"
    cognito_endpoint: str        # e.g. "https://cognito-idp.eu-west-1.amazonaws.com/"
    cognito_client_id: str       # public app-client id for that region's user pool
    user_agent: str | None = None  # None => fall back to the shared default;
                                     # set only if a region needs its own (see §0.3)

REGIONS: Final[dict[str, RegionConfig]] = {
    "emea": RegionConfig(
        key="emea",
        label="Europe / Middle East / Africa (EMEA)",
        base_url="https://api.fluidra-emea.com",
        cognito_endpoint="https://cognito-idp.eu-west-1.amazonaws.com/",
        cognito_client_id="g3njunelkcbtefosqm9bdhhq1",
    ),
    # "napac": RegionConfig(...)   # added once a capture confirms the values
}
DEFAULT_REGION: Final = "emea"
```

Derived endpoints (`USER_POOLS_ENDPOINT`, `DEVICES_ENDPOINT`,
`CONSUMER_PROFILE_ENDPOINT`, and the inline pool/component URLs in §0.2)
become `f"{region.base_url}/..."` built at call time from
`FluidraPoolAPI`'s region, instead of module-level `Final` strings. This
touches every consumer listed in §0.2 — that's the real size of the future
implementation, not just `_constants.py`.

`FluidraPoolAPI.__init__` (`fluidra_api/client.py:31-38`) currently takes
`email, password, hass=None, refresh_token=None, on_token_persist=None` with
**no region parameter at all**. It would gain a `region: str = DEFAULT_REGION`
(or a resolved `RegionConfig`) argument, threaded down through the mixins
that currently import the module constants directly (`AuthMixin`,
`DevicesMixin`, `ComponentsMixin`, `SchedulesMixin`).

## 2. Config flow

- Add a region selector to `async_step_user` (`config_flow.py:75-108`),
  using HA's `SelectSelector` populated from `REGIONS`, **defaulting to
  `"emea"`** so existing installs and the current STEP_USER_DATA_SCHEMA
  behavior are unaffected for anyone who doesn't touch the field.
- Store the chosen key in `entry.data["region"]` (new key, not currently
  present — no existing entry has it).
- **Migration of existing entries**: `custom_components/fluidra_pool/__init__.py:243-264`
  already has an `async_migrate_entry` with an explicit placeholder comment
  ("Version 1 -> 2: Reserved for future migrations") and `config_flow.py:55`
  currently pins `VERSION = 1`. The migration is mechanical:
  ```python
  if entry.version == 1:
      data = {**entry.data, "region": DEFAULT_REGION}
      hass.config_entries.async_update_entry(entry, data=data, version=2)
  ```
  This guarantees every entry created before multi-region support is treated
  as EMEA (matches reality — the integration has only ever talked to EMEA)
  without asking the user anything.
- `entry.data.get("region", DEFAULT_REGION)` should also be used as a
  read-time fallback anywhere the region is consumed, as defense in depth
  in case a migration is ever skipped (e.g. entry imported via YAML/backup
  restore from an unexpected path).

## 3. Impact on reconfigure / reauth

**Recommendation: region is not editable from `async_step_reconfigure`
(`config_flow.py:223-275`) or from reauth (`config_flow.py:163-221`).**

Reasoning:
- A myFluidra/Fluidra Connect **account is created in, and permanently
  belongs to, one region's Cognito user pool** (this is exactly the
  mechanism behind issue #91 — the Australian account simply does not exist
  in the EMEA pool, and vice versa). Changing "region" for an existing
  account is not a real user operation — it would either be a no-op (same
  account, same region) or guaranteed to fail (Cognito would reject the
  credentials against the wrong pool, indistinguishable from a typo'd
  password).
- Reauth exists to refresh a rejected/expired token for the *same* account
  (see `_test_credentials` / `_verify_mfa` reusing `self._pending_email`
  and the `_abort_if_unique_id_mismatch()` guard at `config_flow.py:135,196`)
  — the region is a property of that account, not something reauth should
  ever need to change.
- Reconfigure allows changing the account's *email* (see
  `config_flow.py:248-250`), but swapping to a different Fluidra account
  that happens to live in another region is effectively "delete and
  re-add" territory — safer to require the user to remove and re-create the
  entry, so the new region is chosen explicitly at step `user` rather than
  silently inherited or silently reset to default.
- Consequence: the region field should be **read-only after creation** (no
  selector shown in `async_step_reconfigure`'s form) and not part of the
  reauth data payload at all.

## 4. Test strategy

- **Region wiring in isolation** — a parametrized test that iterates
  `REGIONS` (even with a single `"emea"` entry today) and constructs a
  `FluidraPoolAPI` per region, verifying that the API layer resolves URLs
  from `region.base_url` and not from a hardcoded `FLUIDRA_EMEA_BASE`. This
  is the main thing that *can* be tested today without a real second region:
  it exercises the parameterization path, not the actual APAC/NA values.
- **Config flow** — HA test patterns already used elsewhere in `tests/`
  (`hass.config_entries.flow.async_init` / `async_configure`) extend
  naturally: assert the default selection is `"emea"`, assert the stored
  `entry.data["region"]` matches the selection, assert
  `async_step_reconfigure` does not expose/accept a region field.
- **Migration** — construct a `MockConfigEntry` at `version=1` without a
  `region` key (simulating every entry that exists today) and assert
  `async_migrate_entry` yields `version=2` with `region == "emea"`.
- **Existing constant-importing tests need updating regardless of a second
  region.** `tests/test_fluidra_api_auth.py:17,412` imports
  `FLUIDRA_USER_AGENT` directly from `_constants` and asserts headers equal
  it. Once the user agent (and the other constants) move from module-level
  `Final` values into `REGIONS["emea"].*`, those tests must be updated to
  read through `REGIONS[...]` — this is a certain, mechanical cost
  independent of whether NA/APAC values ever arrive, and should be budgeted
  into the *first* implementation PR (the one that only refactors EMEA to
  go through `RegionConfig`, with zero new regions yet).
- **What remains unverifiable without a volunteer**: whether a second
  region's Cognito pool accepts the exact same `InitiateAuth`/
  `RespondToAuthChallenge` shapes, whether its data-plane API returns the
  same JSON shapes for pools/devices/components (see §5), and whether
  `format=tree` / `deviceType=connected` / the User-Agent are accepted as-is.
  These can only be mocked with *assumed* values — any such mock would be
  validating our guess, not the real backend, so this spike deliberately
  does not add "APAC" mocks with invented data.

## 5. Risks

- **API shape drift beyond URLs.** Nothing guarantees a second region's
  backend returns identical JSON shapes for `/generic/devices` (`format=tree`),
  `/generic/pools/{id}/status`, or component payloads. Fluidra likely runs
  the same backend software per region (same vendor, same mobile app talking
  to all regions), which makes shape parity *plausible*, but issue #91 gives
  zero evidence either way — this is exactly the kind of assumption the plan
  forbids guessing on. A first capture must include at least one full
  device/component response to check field-for-field.
- **Certificate pinning is unknown.** If the official app pins its TLS
  certificate, a mitmproxy-based capture won't work without a legitimate
  on-device bypass (rooted/jailbroken device, or app-specific unpinning) —
  this could turn "ask a volunteer to capture traffic" into "ask a volunteer
  who also knows how to defeat cert pinning," raising the bar significantly.
  Flagged explicitly in the capture guide (§ prerequisites) as the first
  thing a volunteer should verify.
- **Maintenance cost of a region with no active testers.** Once a region is
  added, every future change to `_auth.py`/`_devices.py`/`_components.py`/
  `_schedules.py` risks silently breaking it, since CI cannot exercise a real
  APAC/NA account. Realistic mitigation: keep per-region integration tests
  mock-only (verifying *this codebase's* request construction, not the
  remote's response), and treat "unverified since capture" as a standing,
  documented caveat for any non-EMEA region — similar to how the EMEA-only
  limitation is called out today in the README.
  (`README.md:151-158`).
- **Single capture is a sample size of one.** A single volunteer's capture
  confirms *their* account works with the values seen, but Fluidra could
  still run more than one backend per broad region (e.g. NA vs. LATAM, or
  further APAC sub-splits — issue #91 only distinguishes "North America" and
  "APAC / Australia" informally). The design should keep `REGIONS` open to
  more than 2 entries rather than hardcoding a binary EMEA/non-EMEA switch.
- **Secrets-shaped values in a public issue.** `COGNITO_CLIENT_ID` is safe to
  post publicly (see capture guide), but a careless volunteer could easily
  paste `AccessToken`/`RefreshToken`/`IdToken` or their own email/password
  from the same capture session. This is addressed procedurally in the
  capture guide, not in code, since this plan cannot enforce redaction.

## 6. Launch criteria — what a capture must provide before implementation starts

Minimum data set needed to open a real (non-spike) implementation plan for a
new region:

- [ ] Region data-plane base host (equivalent of `FLUIDRA_EMEA_BASE`),
      confirmed from at least 2 different API calls in the capture (not just
      the login).
- [ ] Cognito IdP endpoint hostname, i.e. the AWS region
      (`cognito-idp.<aws-region>.amazonaws.com`).
- [ ] Cognito app-client id (the `ClientId` field of a captured
      `InitiateAuth` request body) — safe to capture and post as-is, it's
      public by design.
- [ ] Confirmation of the `AuthFlow` used in `InitiateAuth` (matches the
      current `USER_PASSWORD_AUTH`-style flow, or different) and whether MFA
      challenge names match what `_auth.py` already handles.
- [ ] At least one full, redacted JSON response body for each of:
      `/generic/users/me/pools`, `/generic/devices?format=tree&deviceType=connected`,
      `/generic/pools/{id}`, `/generic/pools/{id}/status`,
      `/mobile/consumers/me`, and one `/generic/devices/{id}/components/{id}`
      — to check shape parity against the EMEA responses this codebase
      already parses (§5, first risk).
- [ ] Whether the `User-Agent` sent by the official app for that region
      matches `FLUIDRA_USER_AGENT` verbatim, or differs (app package name,
      version format) — resolves the §0.3 unknown.
- [ ] A volunteer test account able to stay engaged through at least one
      review/testing round-trip (read-only calls are enough to start; a
      write/command call, e.g. toggling a component, is needed before
      declaring the region supported, not just "detected").
- [ ] Explicit note on whether certificate pinning had to be bypassed to
      capture the traffic, and how (informs whether other volunteers can
      reproduce this cheaply).

Until every checked item exists for a given region, that region stays
unimplemented — per this plan's scope, no guessing/enumeration is allowed to
fill gaps.
