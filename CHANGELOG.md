# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
