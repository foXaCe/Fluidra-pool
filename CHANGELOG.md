# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [2.28.0] - 2026-04-19

### Added
- **CC24042517 — AstralPool Clear Connect Evo 21g** chlorinator support (Issue #51, reported by @benoitma)
  - Full mapping for pH (component 165, ÷100), ORP (170), water temperature (172, ÷10), salinity (174, ÷100) and free chlorine (178, ÷100)
  - Chlorination level on component 10, pH/ORP setpoints on 16/20, boost mode on component 103
  - Previously fell back to the generic chlorinator config, which misread the temperature as pH

## [2.27.1] - 2026-04-18

### Fixed
- **HTTP client connection leaks and dangerous fallbacks** (`fluidra_api.py` rewrite)
  - Responses were returned without `async with`/`release()`, eventually saturating the aiohttp connection pool on long-running HA instances
  - `close()` could shut down the session shared with every other HA integration when the API was reused outside the HA lifecycle
  - `set_heat_pump_temperature` fell back to components 12/13/14/16 on failure — component 13 is the pump ON/OFF, so a retry could silently switch the heat pump off instead of adjusting the setpoint
  - A hidden `test_pool` fallback exposed a fake device in the UI when the account returned no pools
  - Tokens are now refreshed from `_request` without the call-site recursion that could loop
- **Home Assistant crash on repair flow** (`repairs.py`)
  - `connection_error` was declared `is_fixable=True` with no `async_create_fix_flow`, so clicking "Fix" in the UI raised `ValueError`
- **`FluidraHeaterSwitch` was a no-op**
  - Constructor was called with the wrong arity and `async_turn_on/off` used `hasattr(dict, "turn_on")`, which is always false
- **Entities were mutating shared coordinator data**
  - Switch, number, select and light platforms wrote optimistic state directly into `coordinator.data`, which raced with the poll cycle and bypassed state-change notifications; all optimistic state is now local
- **Retry loop ignored HTTP 429/5xx**
  - Transient server errors (503, rate limiting) are now retried with `Retry-After` honoured, instead of surfacing as "device unavailable"
- **Schedules interpreted in the wrong time zone**
  - `_calculate_auto_speed_from_schedules` used `datetime.now()` (system TZ) instead of `dt_util.now()` (HA TZ)
- **One broken pool marked every entity unavailable**
  - `_async_update_data` wrapped the whole poll in a single try/except; per-pool failures are now isolated and keep previous data

### Changed
- **`device_id` / `pool_id` URL-encoded** in every endpoint path (`fluidra_api.py`) to stop path injection through user-supplied service payloads
- **Logs redact credentials** via new `mask_email` / `mask_device_id` helpers; response bodies containing tokens are no longer echoed to the warning logs
- **Dependencies**: drop unused `PyJWT` from `manifest.json`, raise `aiohttp` floor to `3.11` to match HA Core
- **Quality Scale**: add `quality_scale.yaml` declaring the `silver` level with explicit `todo`/`exempt` annotations for the rules not yet met
- **`config_flow` exception handling** narrowed to `FluidraAuthError` / `FluidraConnectionError` / `aiohttp.ClientError` so transient errors no longer look like bad credentials

### Performance
- **`identify_device` cached** on the device dict keyed by the component-7 signature, and wildcard patterns compiled through `functools.lru_cache` — reduces per-tick work from thousands of regex matches to near-zero

### Removed
- Dead classes and no-op properties: `FluidraSpeedControl`, `FluidraPumpComponentNumber`, `entity_picture` returning `None`, duplicated `device_class = "switch"` string literals, the unused `UPDATE_INTERVAL` constant in `__init__.py`
- `_cached_device_data` / `_cached_pool_data` anti-pattern on the sensor base class

### Translations
- **MFA step** and `invalid_mfa_code` error added to `en.json`, `es.json`, `pt.json` (previously only French was complete; other locales showed the raw keys when MFA was required)

## [2.27.0] - 2026-04-17

### Added
- **LC25007119 chlorinator support** (PR #49 by @keltakmaster)
  - Same mappings as other LC models (pH, ORP, temperature, salinity)

### Changed
- Removed EXO debug scan and component log dump (Issue #39 — investigation complete, cover mode not exposed by Fluidra API)
- CI: bump `softprops/action-gh-release` from v2 to v3 (Node 24)

## [2.26.2] - 2026-04-14

### Fixed
- **No more reauth prompts on transient network errors** (Issue #29)
  - DNS timeouts, Cognito unreachable, or short internet outages (Starlink micro-outages, etc.) were incorrectly triggering the reauth prompt
  - Connection errors (`FluidraConnectionError`, `FluidraCircuitBreakerError`) now propagate as `UpdateFailed` so the coordinator simply retries on the next polling cycle
  - Reauth flow is now only triggered for actual auth failures (invalid credentials, MFA required)

## [2.26.1] - 2026-04-13

### Changed
- **Debug scan for EXO chlorinators** (Issue #39)
  - Extended component scan to 0-200 on NS25 EXO devices
  - Added WARNING log dumping all non-null components per polling cycle
  - Temporary diagnostic release to find the "actual production" component for cover mode detection

## [2.26.0] - 2026-04-12

### Added
- **MFA / 2FA authentication support** (PR #43 by @PhilJung)
  - Handles Cognito MFA challenges (SOFTWARE_TOKEN_MFA, SMS_MFA)
  - New MFA verification step in config flow with translated UI (EN/FR)
  - Persists refresh token in config entry data to bypass MFA on HA restart
- **LC24015802 iSALT 7 chlorinator support** (PR #44 by @PhilJung)
  - Bridged tecnoLC2 device with full pH/ORP/temp/salinity sensors
- **CC24041107 chlorinator support** (Issue #36 reported by @StenGarny)
  - Added to the Energy Connect bridged chlorinator family

## [2.25.1] - 2026-04-11

### Fixed
- **Token refresh loop causing reauthentication** (Issue #29)
  - Fluidra Cognito returns short-lived tokens (ExpiresIn=300s)
  - Fixed 300s safety margin made tokens expire immediately, triggering ~50 refreshes per polling cycle (3722 in a single user log)
  - Use adaptive margin: `min(300, max(30, expires_in // 10))` → 30s for short tokens, 300s for long tokens
  - Add `asyncio.Lock` with double-check pattern to serialize concurrent refresh attempts from parallel requests

## [2.25.0] - 2026-04-10

### Added
- **Full Zodiac Z260iQ feature exposure** (PR #42 by @h3nnes)
  - Air temperature sensor (c67) now properly exposed as HA entity
  - Running hours sensor (c0) with TOTAL_INCREASING state class
  - No-flow alarm state tracking (c28)
  - HVAC mode control via c13 (ON/OFF) + c14 (mode/preset)
  - Heat/Cool/Heat+Cool modes with proper preset preservation
  - Custom temperature range (7-40°C, 1°C step)
  - BXWAD component 7 signature check to differentiate from Z250iQ (both use LF* prefix)

## [2.24.0] - 2026-04-08

### Added
- **Zodiac Z260iQ heat pump support** (Issue #41)
  - Combined mode/preset via c14 (smart/boost/silence × heat/cool/auto)
  - Air temperature sensor via generic `air_temp_component` handling
  - Status c17, no flow alarm c28

### Fixed
- **Detailed auth logging** for diagnosing reauthentication issues (Issue #29)
  - Logs token expiration, refresh attempts, re-auth fallback, and exact failure reason
  - Helps users provide actionable debug data

## [2.23.0] - 2026-03-27

### Added
- **CC24054221 Energy Connect bridged chlorinator support** (Issue #36 by @cortalys)
  - ON/OFF control via c0, pH setpoint c157 (÷10), salinity c160 (÷1000)
  - Custom sensor divisors for non-standard component formats

### Fixed
- **Auto-retry with token refresh on HTTP 401** — when token expires mid-polling during parallel requests, each request now automatically refreshes the token and retries once instead of failing silently
- **Chlorinator ON/OFF switch** reads state from `on_off_component` (device-specific) instead of always using c9

## [2.22.0] - 2026-03-19

### Added
- **CC24058902 chlorinator support** (Issue #35 by @Enkil13)
  - pH, ORP (c177), free chlorine (c178), temperature, salinity sensors

### Changed
- Add `PARALLEL_UPDATES = 0` to all 7 entity platforms (coordinator-based optimization)

### Fixed
- Config flow tests now mock `_cognito_initial_auth` instead of `authenticate` (119/119 tests pass)

## [2.21.0] - 2026-03-17

### Added
- **CC24017504 chlorinator support** (PR #34 by @Profusio83)
  - Energy Connect tecnoLC2 with pH/ORP probes
  - Same component mappings as CC24068402

## [2.20.0] - 2026-03-17

### Added
- **CC24068402 Energy Connect chlorinator support** (Issue #33)
  - AstralPool Energy Connect (tecnoLC2 with pH/ORP probes)
  - pH, ORP, temperature, salinity, chlorination actual sensors
  - Correct component mappings (c165, c170, c172, c174, c154)

## [2.19.1] - 2026-03-16

### Fixed
- **Boost switch unavailable for chlorinators without mode select** (Issue #25)
  - Boost switch was always marked `unavailable` for devices with `skip_mode_select` (CC24042711, CC25005502, all LC models) because mode component c20 was null
  - Skip unnecessary mode ON command before activating boost on these devices

## [2.19.0] - 2026-03-14

### Added
- **LC24013306 Irripool iSALT chlorinator support** (Issue #31)
  - pH, ORP, temperature, salinity sensors with correct component mappings

### Fixed
- **Diagnostics 500 Internal Server Error** — removed reference to non-existent `_optimistic_entities` attribute that caused diagnostics download to fail

## [2.18.0] - 2026-03-08

### Added
- **LC24056317 Gre chlorinator support** (Issue #28)
  - Gre chlorinator (I.D. Electroquimica/Fluidra) device config
  - pH, temperature, salinity sensors (no ORP on this model)
  - User-tested and validated

### Fixed
- **Token refresh fallback to full re-authentication** (Issue #29)
  - When Cognito refresh token expires (~30 days), the integration now re-authenticates with stored credentials instead of showing a reauth prompt
  - Reauth flow now tests only Cognito auth, preventing false "invalid credentials" errors from transient API issues
  - Better logging for token refresh failures

### Changed
- Updated codeowner and documentation URLs

## [2.17.0] - 2026-03-03

### Added
- **CC24042711 tecnoLC2 chlorinator support** (Issue #25)
  - AstralPool Clear Connect non-scalable device config
  - Correct temperature mapping (c172 instead of c183)
  - Correct salinity mapping (c174 instead of c185)
  - No pH/ORP sensors (device has no probes)
  - Chlorination actual production sensor (c154)

## [2.16.0] - 2026-02-26

### Added
- **EXO iQ35 schedule output select** — schedule selects now show hardware outputs (Pump/Aux 1/Aux 2) instead of speed levels for EXO chlorinators
  - Detects `schedule_output_type` from device config to adapt options, read and write logic
  - Supports both `componentActions` (EXO) and `operationName` (DM) API formats
  - Translations in EN, FR, ES, PT

### Changed
- GitHub release workflow now uses CHANGELOG.md as release notes source

## [2.15.0] - 2026-02-26

### Added
- **Zodiac EXO iQ35 chlorinator** (NS25*) full support (Issue #24)
  - Device detection for WiFi-direct chlorinators (NS25* pattern)
  - Component mappings: pH, ORP, temperature, salinity, schedules
  - Chlorinator ON/OFF switch via `on_off_component`
  - Dynamic chlorination level range (0-10 for EXO vs 0-100% for CC*)
  - Custom sensor divisors (salinity in mg/L)
- 122 new tests (was 4): api_resilience, device_registry, config_flow, coordinator, base entities

### Changed
- **Full i18n refactoring** — all sensor names and states now use HA's built-in translation system
  - Removed hardcoded `name` properties and French strings from all sensor classes
  - Added `_attr_translation_key` + `_attr_has_entity_name = True` on all sensors
  - Converted `speed_mode`, `pool_status`, `water_quality`, `device_info` sensors to `SensorDeviceClass.ENUM` with translated states
  - Converted schedule sensor from text to numeric (returns count as `int`)
  - Added translation keys for all 6 chlorinator sensors
  - Converted `device_info` dicts to `DeviceInfo` objects across sensor, entity, and chlorinator classes
- **Code quality refactoring**
  - Created base entity classes (`entity.py`) to eliminate duplication across 6 platform files
  - Extracted `api_resilience.py` from `fluidra_api.py` (circuit breaker, rate limiter, exceptions)
  - Centralized scattered constants in `const.py` (LG presets, LumiPlus IDs)
  - Fixed 34 bare `except Exception: pass` patterns with specific exceptions and debug logging
  - Fixed `ConfigEntryAuthFailed` being swallowed by generic except in coordinator (broke reauth flow)
- Complete Spanish (es.json) and Portuguese (pt.json) translations synchronized with all 136 translation keys

### Fixed
- Chlorinator switch `unique_id` collision — added unique suffix
- Hassfest validation: lowercase schedule speed state keys (`S1`→`s1`, `S2`→`s2`, `S3`→`s3`)
- Coordinator component 20 handling for chlorinator schedule lists

## [2.14.2] - 2025-02-03

### Fixed
- Add support for LC24019518 chlorinator (Issue #21)

### Changed
- Pre-commit autoupdate

## [2.14.1] - 2025-01-23

### Fixed
- Duplicate URL parameter in API component control request
- API session not closed after credential test in config flow
- Python 3.11+ compatibility (TypeAlias instead of `type` keyword)
- Private attribute access (`api._pools` → `api.cached_pools`)
- Dead code in exception handlers
- Missing `CoordinatorEntity` import at runtime
- Unused variables in sensor and select platforms
- Service response key alignment with schema

## [2.14.0] - 2025-01-22

### Added
- Platinum Level - Full Home Assistant Quality Scale compliance
- Circuit breaker pattern (5 failures → 5 min pause)
- Rate limiting with sliding window
- Retry with exponential backoff and jitter
- `py.typed` marker for strict typing support
- `SupportsResponse.OPTIONAL` on all services for automation responses
- `__slots__` on all entity classes for memory optimization
- `logbook.py` for event descriptions
- `.devcontainer/` for instant development setup
- `async_migrate_entry` for future config migrations
- Complete CI/CD pipeline (hassfest, HACS, releases)
- MIT License

### Changed
- Upgraded to `runtime_data` pattern
- Services now return `ServiceResponse` with success/error info
- Minimum Home Assistant version: 2024.4.0
- Improved startup performance (non-blocking first refresh)
- Extracted timing constants for maintainability
- Moved inline imports to module level

### Fixed
- `hacs.json` iot_class alignment with manifest
- Removed invalid `zeroconf`/`ssdp` discovery (cloud-only API)
- `_format_cron_time_chlorinator` method in base class

## [2.13.0] - 2025-01-19

### Added
- Gold Level - Reconfigure flow for credential updates
- Options flow for configurable scan interval
- Reauth flow for expired tokens
- Diagnostics with proper data redaction
- Repair issues for offline devices and firmware updates

### Changed
- Config flow now uses `async_update_reload_and_abort`
- Options listener reloads integration on change

## [2.12.0] - 2025-01-17

### Added
- Z550iQ+ heat pump full support (heating/cooling modes)
- LumiPlus Connect RGBW light control
- DM24049704 chlorinator schedule support
- Spanish translation (es.json)

### Fixed
- Remove invalid 'state' key from strings.json (hassfest)
- Add LE* pattern for E30iQ pumps (device LE24500883)
- Sync strings.json with fr.json, add missing keys and state translations
- Add missing domains to hacs.json (climate, time, light)

## [2.11.0] - 2024-12

### Added
- Z550iQ+ heat pump support with preset modes
- Optimistic updates for chlorinator schedule entities

### Fixed
- Switch optimistic updates and error handling

## [2.10.4] - 2024-12

### Added
- Schedule speed control for DM24049704 chlorinator
- Support for DM24049704 Domotic S2 chlorinator (SheepPool)

### Fixed
- DM24049704 schedule API format to match official app
- DM24049704 real-time updates
- Schedule time parsing for CRON and numeric formats

## [2.9.0] - 2024-11

### Added
- Support for CC25005502 chlorinator model (Issue #15)
- Support for CC25013923 chlorinator model (Issue #14)
- LumiPlus Connect effect/scene selector (component 18)
- LumiPlus Connect scheduler support (component 40)
- LumiPlus effect speed slider (1-8)

### Fixed
- Light schedule entities - use DeviceIdentifier
- Light effect selector and add optimistic state for light control
- Exclude pump select entities from light devices
- Only create light schedule entities for existing schedules

## [2.8.0] - 2024-11

### Added
- LumiPlus Connect RGBW light support

## [2.7.0] - 2024-10

### Changed
- Clean up repository and switch README to English
- Add development tooling and documentation

## [2.6.0] - 2024-10

### Added
- Ruff linting and code formatting

## [2.5.54] - 2024-10

### Fixed
- IndentationError issues in number.py and time.py
- IndentationError in __init__.py

## [2.5.52] - 2024-10

### Added
- Support for CC24018202 chlorinator model
- Support for CC25002928 chlorinator (Energy Connect 21 Scalable)

### Changed
- Clean up excessive logging - reduce logs by 98%

### Fixed
- CC24033907 sensor component mappings
- Remove free chlorine sensor from CC25002928

## [2.5.47] - 2024-09

### Added
- Support for CC24021110 chlorinator
- Support for CC25113623 chlorinator
- pH, ORP and free chlorine sensors for CC24033907 chlorinator
- pH and ORP setpoint controls for chlorinator

### Fixed
- Salinity sensor component for CC25113623 chlorinator
- Temperature sensor component for CC25113623 chlorinator
- Use measured value components for CC24033907 pH/ORP sensors
- Chlorination values rounded to nearest multiple of 10
- Use 10% increments for CC24033907 chlorination slider
- Use desiredValue for all sliders to prevent state reversion
- Temperature sensor and boost switch improvements
- Optimistic state for chlorinator sliders

### Changed
- Automatic cleanup of removed devices
- Remove emojis from error logs
- Improve startup performance (perf event for full scan)

## [2.5.0] - 2024-08

### Added
- Complete chlorinator support with bridged devices
- Translations for chlorinator mode and pool status
- Portuguese translation

### Changed
- Remove manual translation from pump speed sensor
- Use brand logo from Home Assistant brands repository

### Fixed
- Pump speed states translation

## [2.4.0] - 2024

### Added
- Initial release with pool device support

[2.16.0]: https://github.com/foXaCe/Fluidra-pool/compare/v2.15.0...v2.16.0
[2.15.0]: https://github.com/foXaCe/Fluidra-pool/compare/v2.14.2...v2.15.0
[2.14.2]: https://github.com/foXaCe/Fluidra-pool/compare/v2.14.1...v2.14.2
[2.14.1]: https://github.com/foXaCe/Fluidra-pool/compare/v2.14.0...v2.14.1
[2.14.0]: https://github.com/foXaCe/Fluidra-pool/compare/v2.13.0...v2.14.0
[2.13.0]: https://github.com/foXaCe/Fluidra-pool/compare/v2.12.0...v2.13.0
[2.12.0]: https://github.com/foXaCe/Fluidra-pool/compare/v2.11.0...v2.12.0
[2.11.0]: https://github.com/foXaCe/Fluidra-pool/compare/v2.10.4...v2.11.0
[2.10.4]: https://github.com/foXaCe/Fluidra-pool/compare/v2.9.0...v2.10.4
[2.9.0]: https://github.com/foXaCe/Fluidra-pool/compare/v2.8.0...v2.9.0
[2.8.0]: https://github.com/foXaCe/Fluidra-pool/compare/v2.7.0...v2.8.0
[2.7.0]: https://github.com/foXaCe/Fluidra-pool/compare/v2.6.0...v2.7.0
[2.6.0]: https://github.com/foXaCe/Fluidra-pool/compare/v2.5.54...v2.6.0
[2.5.54]: https://github.com/foXaCe/Fluidra-pool/compare/v2.5.52...v2.5.54
[2.5.52]: https://github.com/foXaCe/Fluidra-pool/compare/v2.5.47...v2.5.52
[2.5.47]: https://github.com/foXaCe/Fluidra-pool/compare/v2.5.0...v2.5.47
[2.5.0]: https://github.com/foXaCe/Fluidra-pool/compare/v2.4.0...v2.5.0
[2.4.0]: https://github.com/foXaCe/Fluidra-pool/releases/tag/v2.4.0
