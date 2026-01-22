# Changelog

All notable changes to Fluidra Pool Integration will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [2.14.0] - 2025-01-22

### Added
- 🏆 **Platinum Level** - Full Home Assistant Quality Scale compliance
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
- Upgraded to `runtime_data` pattern (Platinum)
- Services now return `ServiceResponse` with success/error info
- Minimum Home Assistant version: 2024.4.0
- Improved startup performance (non-blocking first refresh)
- Extracted timing constants for maintainability
- Moved inline imports to module level

### Fixed
- `hacs.json` iot_class alignment with manifest
- Removed invalid `zeroconf`/`ssdp` discovery (cloud-only API)
- `_format_cron_time_chlorinator` method in base class (was missing)

## [2.13.0] - 2025-01-19

### Added
- 🥇 **Gold Level** - Reconfigure flow for credential updates
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

### Fixed
- Temperature sensor precision for heat pumps
- Schedule parsing for various device formats

---

## Migration Notes

### From 2.13.x to 2.14.x
No breaking changes. Automatic upgrade.

### From 2.12.x to 2.13.x
No breaking changes. New features available in UI.

---

## Versioning Policy

- **MAJOR**: Breaking changes (config, entities, services removed)
- **MINOR**: New features, backward compatible
- **PATCH**: Bug fixes, performance improvements
