# Architecture — Fluidra Pool integration

Custom Home Assistant integration for Fluidra pool equipment (variable-speed
pumps, heat pumps, salt chlorinators, water analysers, lights) driven through
the Fluidra EMEA cloud API (the one behind the iAquaLink™/Fluidra Pool mobile
app) with AWS Cognito authentication.

## Data flow

```
Fluidra EMEA cloud (REST)  ⇄  fluidra_api/ (client, mixins)
                                   │
                     coordinator/ (DataUpdateCoordinator, 30 s default)
                                   │ coordinator.data  {pool_id: {…, devices: [...]}}
                                   │
      ┌──────────┬──────────┬─────┴────┬──────────┬─────────┬────────┐
   switch/    sensor/   binary_sensor  select/   number   time/   climate/light
      └──────────┴──────────┴──────────┴──────────┴─────────┴────────┘
                    entities read coordinator.data only;
              writes go through api.control_device_component()
```

- **Reads**: only the coordinator polls the cloud. Entities never call the API
  to read state — they consume `coordinator.data`.
- **Writes**: control entities call the API client (`control_device_component`,
  `set_schedule`, …) then request a debounced coordinator refresh. Most keep a
  short optimistic local state (see *Optimistic state* below).
- **First refresh** is intentionally minimal (pool list only) for a fast
  startup; the first full component poll happens on the next cycle.

## Directory map

| Path | Role |
|---|---|
| `__init__.py` | Config-entry lifecycle (`runtime_data`), device-registry seeding, 3 schedule services |
| `const.py` | All constants — domain, device types, component ids with a single stable meaning, timing/thresholds, `FluidraPoolRuntimeData` |
| `coordinator/coordinator.py` | Polling orchestration, per-pool failure isolation, component-state decoding, stale-device purge (3-strike), `connection_error` repair wiring, firmware→registry mirror |
| `coordinator/_parsers.py` | Pure parsing helpers (DM24049704 program format, auto-speed from schedules) |
| `fluidra_api/` | Standalone API client assembled from mixins: `_session` (single `_request` entry: timeout, retry/backoff, refresh-on-401, circuit breaker, rate limiter), `_auth` (Cognito login/MFA/refresh), `_devices` (discovery + polling), `_components` (get/set component), `_schedules`, `_commands` |
| `api_resilience.py` | Typed exception hierarchy + `CircuitBreaker`/`RateLimiter` |
| `device_registry/` | Device identification: `identifier.py` (pattern matching + priority), `types.py` (`DeviceConfig`), `configs/` (per-family profiles; chlorinators use the `_standard_tecnolc2()` factory) |
| `entity.py` | `FluidraPoolEntity` / `FluidraPoolControlEntity` bases (`device_data`, `device_info` incl. `sw_version`, `available`) |
| `helpers.py` | Pure shared functions (`get_schedule_data`, `resolve_component_rw`, `parse_cron_time`) — no `hass`, no I/O |
| `utils.py` | Pure helpers predating `helpers.py` (cron days, masking) |
| Platform packages (`switch/`, `sensor/`, `select/`, `time/`) and modules (`climate.py`, `light.py`, `number.py`, `binary_sensor.py`) | `__init__.py` of each platform only dispatches device→entity classes (incl. the dynamic-devices listener); entity logic lives in the per-type modules |
| `config_flow.py` | user / MFA / reauth / reconfigure steps + options flow (`scan_interval`) |
| `diagnostics.py`, `repairs.py` | Redacted diagnostics; fixable `connection_error` repair |

## Key mechanisms

### Device identification (`device_registry/`)
Each cloud device is matched against `DEVICE_CONFIGS` by serial/name/family
patterns, highest `priority` first. The matched `DeviceConfig` drives which
entities are created (`entities` + feature gates), which components are polled
(`specific_components` scoping) and how values are decoded (`features` such as
`sensors`, `ph_setpoint`, `info_layout`). **The identify result is cached** per
device signature.

Component IDs mean different things per family (e.g. c19 = timezone on pumps
but water temperature on heat pumps) — that is why the coordinator keys off
commented raw integers and only globally-stable ids get a `COMPONENT_*` name in
`const.py`.

### Optimistic state
Control entities show the requested value immediately, then either confirm it
from coordinator data or let it expire (`OPTIMISTIC_ACTION_TIMEOUT` 10 s,
`CLIMATE_OPTIMISTIC_TIMEOUT` 5 s, `CHLORINATOR_MODE_OPTIMISTIC_TIMEOUT` 120 s —
tecnoLC2 cells report mode changes back very slowly).

### Error convention (project rule)
Every control method (`async_turn_on/off`, `async_select_option`,
`async_set_native_value`, …) must, on API failure **or** `success is False`:
clear its optimistic state, then raise
`HomeAssistantError(translation_domain=DOMAIN, translation_key=…)`.
User-input rejections (e.g. changing speed while auto mode is active) raise
`ServiceValidationError` instead.

### Resilience
- One `_request()` funnel: 30 s timeout, exponential backoff on 429/5xx,
  automatic token refresh on 401 (double-checked locking), circuit breaker +
  rate limiter for the Fluidra data plane.
- Cognito calls **bypass the circuit breaker on purpose** — different host;
  an EMEA outage must not block re-authentication.
- Per-pool refresh failures degrade gracefully (previous data kept). A cycle
  where *every* pool fails counts toward the `connection_error` repair issue
  (raised at 3 consecutive failures, cleared on recovery).
- Stale devices are purged only after 3 consecutive successful polls without
  them (`STALE_DEVICE_THRESHOLD`), so a partial cloud response cannot wipe
  entities/history.

### Hard rules
- **Never change an existing entity `unique_id`.** Formats are heterogeneous
  (`fluidra_{device}_…` vs `fluidra_pool_{pool}_{device}_…`) but each is
  stable; changing one orphans user entities. Any future change requires
  `async_migrate_entry` + `ConfigFlow.VERSION` bump + tests.
- Entity `available` gates on `device["online"]` — known first-cycle
  limitation, deliberately kept until real device captures allow a safe fix.
- Z250iQ/Z260iQ identification via component 7 is no-touch without captures.

## How to add a new device model

1. Get diagnostics from the reporter (component values while the equipment runs).
2. Add a profile in `device_registry/configs/<family>.py` — for a standard
   tecnoLC2 chlorinator this is one `_standard_tecnolc2([...serials...],
   priority=NN)` call; otherwise a full `DeviceConfig` with documented
   component mapping (keep the issue number and reporter in a comment).
3. Add the serial patterns to `identifier_patterns` (highest priority wins on
   overlap — check `tests/test_device_registry.py`).
4. Add a test pinning the identification + feature mapping.

## How to add a new platform

1. Create the platform module/package with `async_setup_entry` following the
   existing pattern: initial creation from `coordinator.api.cached_pools`,
   plus a `coordinator.async_add_listener` callback registered via
   `config_entry.async_on_unload(...)` that adds entities for newly-seen
   devices (dynamic-devices, no reload needed).
2. Entity classes inherit `FluidraPoolEntity` (read-only) or
   `FluidraPoolControlEntity` (writes) and follow the error convention.
3. Add the platform to `PLATFORMS` in `__init__.py`, translations under
   `entity.<platform>.<translation_key>` in all 5 files, and tests.

## Tests

`tests/` (pytest + pytest-homeassistant-custom-component): 1286 tests, 98 %
coverage, every module ≥ 90 % (CI gate: 90 %). `conftest.py` provides
`mock_api` (spec'd `AsyncMock` with wired instance attributes) and
`mock_pool_data`. mypy runs `--strict` (Platinum), ruff lints/formats, hassfest
validates on an isolated workspace.
