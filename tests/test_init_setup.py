"""Integration-style tests for custom_components.fluidra_pool.__init__.

Covers the config-entry lifecycle (setup/unload), the error paths that map
FluidraError/OSError/TimeoutError to ConfigEntryNotReady and
FluidraMFARequired to ConfigEntryAuthFailed, the three schedule services,
the options-update reload listener, and the pure time/schedule helpers.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import (
    ConfigEntryAuthFailed,
    ConfigEntryNotReady,
    HomeAssistantError,
    ServiceValidationError,
)
from homeassistant.helpers import device_registry as dr
import pytest

from custom_components.fluidra_pool import (
    _async_register_services,
    _get_coordinator_for_device,
    _get_device_data,
    _get_schedule_component,
    _parse_service_time,
    _service_schedule_to_fluidra,
    async_migrate_entry,
    async_setup_entry,
    async_unload_entry,
)
from custom_components.fluidra_pool.api_resilience import FluidraError, FluidraMFARequired
from custom_components.fluidra_pool.const import DOMAIN

DEVICE_ID = "E30-001"
POOL_ID = "pool_001"

PATCH_TARGET = "custom_components.fluidra_pool.fluidra_api.FluidraPoolAPI"


def _pools() -> list[dict]:
    """A single pool owning one pump device that identifies cleanly."""
    return [
        {
            "id": POOL_ID,
            "name": "My Pool",
            "devices": [
                {
                    "device_id": DEVICE_ID,
                    "name": "Pool Pump",
                    "family": "",
                    "model": "E30iQ",
                    "type": "pump",
                    "online": True,
                    "components": {},
                },
            ],
        }
    ]


def _prepare_api(mock_api: AsyncMock) -> AsyncMock:
    """Wire the mock API so the coordinator + platform setups stay happy."""
    pools = _pools()
    mock_api.get_pools = AsyncMock(return_value=pools)
    # Platform setups read api.cached_pools directly and iterate it.
    mock_api.cached_pools = pools
    mock_api.set_schedule = AsyncMock(return_value=True)
    mock_api.clear_schedule = AsyncMock(return_value=True)
    mock_api.close = AsyncMock()
    return mock_api


async def _setup(hass: HomeAssistant, mock_api: AsyncMock, **kwargs):
    """Create a MockConfigEntry, set it up through HA, and return it."""
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_EMAIL: "a@b.c", CONF_PASSWORD: "x"},
        unique_id="a@b.c",
        **kwargs,
    )
    entry.add_to_hass(hass)
    with patch(PATCH_TARGET, return_value=mock_api):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    return entry


# --------------------------------------------------------------------------- #
# async_setup_entry — success
# --------------------------------------------------------------------------- #


async def test_setup_entry_loaded(hass: HomeAssistant, mock_api: AsyncMock) -> None:
    """A clean setup leaves the entry LOADED with runtime_data populated."""
    _prepare_api(mock_api)
    entry = await _setup(hass, mock_api)

    assert entry.state is ConfigEntryState.LOADED
    assert entry.runtime_data is not None
    assert entry.runtime_data.coordinator is not None
    mock_api.authenticate.assert_awaited()


async def test_setup_entry_registers_devices(hass: HomeAssistant, mock_api: AsyncMock) -> None:
    """The pool device and the pump device are written to the device registry."""
    _prepare_api(mock_api)
    await _setup(hass, mock_api)

    registry = dr.async_get(hass)
    pool_device = registry.async_get_device(identifiers={(DOMAIN, POOL_ID)})
    pump_device = registry.async_get_device(identifiers={(DOMAIN, DEVICE_ID)})

    assert pool_device is not None
    assert pool_device.manufacturer == "Fluidra"
    assert pump_device is not None
    assert pump_device.model == "Pump"


async def test_setup_entry_no_pools_still_loads(hass: HomeAssistant, mock_api: AsyncMock) -> None:
    """An account with zero pools should still finish setup (LOADED)."""
    _prepare_api(mock_api)
    mock_api.get_pools = AsyncMock(return_value=[])
    mock_api.cached_pools = []
    entry = await _setup(hass, mock_api)

    assert entry.state is ConfigEntryState.LOADED


async def test_setup_entry_device_without_id_skipped(hass: HomeAssistant, mock_api: AsyncMock) -> None:
    """A device dict missing device_id is skipped; setup still LOADED.

    Exercises the `if not device_id: continue` guard in async_setup_entry's
    device-registry update loop.
    """
    _prepare_api(mock_api)
    pools = _pools()
    # A second device with no device_id must not be registered or crash setup.
    pools[0]["devices"].append({"name": "Anonymous", "type": "pump", "components": {}})
    mock_api.get_pools = AsyncMock(return_value=pools)
    mock_api.cached_pools = pools
    entry = await _setup(hass, mock_api)

    assert entry.state is ConfigEntryState.LOADED
    registry = dr.async_get(hass)
    # The named pump is registered, the anonymous one is not.
    assert registry.async_get_device(identifiers={(DOMAIN, DEVICE_ID)}) is not None


# --------------------------------------------------------------------------- #
# async_unload_entry
# --------------------------------------------------------------------------- #


async def test_unload_entry(hass: HomeAssistant, mock_api: AsyncMock) -> None:
    """Unloading sets the entry NOT_LOADED and closes the API client."""
    _prepare_api(mock_api)
    entry = await _setup(hass, mock_api)

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.NOT_LOADED
    mock_api.close.assert_awaited()


async def test_unload_entry_no_runtime_data(hass: HomeAssistant, mock_api: AsyncMock) -> None:
    """Unload tolerates a missing/None runtime_data (no coordinator to close)."""
    _prepare_api(mock_api)
    entry = await _setup(hass, mock_api)
    # Simulate runtime_data already torn down.
    entry.runtime_data = None

    assert await async_unload_entry(hass, entry) is True


# --------------------------------------------------------------------------- #
# error paths during authenticate
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "exc",
    [FluidraError("boom"), OSError("net down"), TimeoutError()],
)
async def test_setup_entry_not_ready(hass: HomeAssistant, mock_api: AsyncMock, exc: Exception) -> None:
    """FluidraError / OSError / TimeoutError -> ConfigEntryNotReady."""
    _prepare_api(mock_api)
    mock_api.authenticate = AsyncMock(side_effect=exc)

    from pytest_homeassistant_custom_component.common import MockConfigEntry

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_EMAIL: "a@b.c", CONF_PASSWORD: "x"},
        unique_id="a@b.c",
    )
    entry.add_to_hass(hass)
    with patch(PATCH_TARGET, return_value=mock_api), pytest.raises(ConfigEntryNotReady):
        await async_setup_entry(hass, entry)


async def test_setup_entry_get_pools_not_ready(hass: HomeAssistant, mock_api: AsyncMock) -> None:
    """A FluidraError from get_pools (after auth) also maps to ConfigEntryNotReady."""
    _prepare_api(mock_api)
    mock_api.get_pools = AsyncMock(side_effect=FluidraError("pools failed"))

    from pytest_homeassistant_custom_component.common import MockConfigEntry

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_EMAIL: "a@b.c", CONF_PASSWORD: "x"},
        unique_id="a@b.c",
    )
    entry.add_to_hass(hass)
    with patch(PATCH_TARGET, return_value=mock_api), pytest.raises(ConfigEntryNotReady):
        await async_setup_entry(hass, entry)


async def test_setup_entry_mfa_required_auth_failed(hass: HomeAssistant, mock_api: AsyncMock) -> None:
    """FluidraMFARequired -> ConfigEntryAuthFailed (reauth)."""
    _prepare_api(mock_api)
    mock_api.authenticate = AsyncMock(side_effect=FluidraMFARequired("SMS_MFA", "session-token"))

    from pytest_homeassistant_custom_component.common import MockConfigEntry

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_EMAIL: "a@b.c", CONF_PASSWORD: "x"},
        unique_id="a@b.c",
    )
    entry.add_to_hass(hass)
    with patch(PATCH_TARGET, return_value=mock_api), pytest.raises(ConfigEntryAuthFailed):
        await async_setup_entry(hass, entry)


# --------------------------------------------------------------------------- #
# services
# --------------------------------------------------------------------------- #


async def test_service_set_schedule(hass: HomeAssistant, mock_api: AsyncMock) -> None:
    """set_schedule forwards to api.set_schedule and returns a success payload."""
    _prepare_api(mock_api)
    await _setup(hass, mock_api)

    response = await hass.services.async_call(
        DOMAIN,
        "set_schedule",
        {
            "device_id": DEVICE_ID,
            "schedules": [
                {
                    "enabled": True,
                    "start_time": "08:00",
                    "end_time": "12:00",
                    "mode": "1",
                    "days": [1, 2, 3],
                }
            ],
        },
        blocking=True,
        return_response=True,
    )

    assert response["success"] is True
    assert response["device_id"] == DEVICE_ID
    assert response["schedules_count"] == 1
    mock_api.set_schedule.assert_awaited()


async def test_service_clear_schedule(hass: HomeAssistant, mock_api: AsyncMock) -> None:
    """clear_schedule forwards to api.clear_schedule and returns success."""
    _prepare_api(mock_api)
    await _setup(hass, mock_api)

    response = await hass.services.async_call(
        DOMAIN,
        "clear_schedule",
        {"device_id": DEVICE_ID},
        blocking=True,
        return_response=True,
    )

    assert response["success"] is True
    assert response["device_id"] == DEVICE_ID
    mock_api.clear_schedule.assert_awaited()


async def test_service_set_preset_schedule(hass: HomeAssistant, mock_api: AsyncMock) -> None:
    """set_preset_schedule builds the standard preset and reports its count."""
    _prepare_api(mock_api)
    await _setup(hass, mock_api)

    response = await hass.services.async_call(
        DOMAIN,
        "set_preset_schedule",
        {"device_id": DEVICE_ID, "preset": "standard"},
        blocking=True,
        return_response=True,
    )

    assert response["success"] is True
    assert response["device_id"] == DEVICE_ID
    assert response["preset"] == "standard"
    # The "standard" preset defines two schedule windows.
    assert response["schedules_count"] == 2
    mock_api.set_schedule.assert_awaited()


# --------------------------------------------------------------------------- #
# options-update reload listener
# --------------------------------------------------------------------------- #


async def test_options_update_triggers_reload(hass: HomeAssistant, mock_api: AsyncMock) -> None:
    """Changing options reloads the entry (options snapshot differs)."""
    _prepare_api(mock_api)
    entry = await _setup(hass, mock_api)
    assert entry.state is ConfigEntryState.LOADED

    with patch(PATCH_TARGET, return_value=mock_api):
        hass.config_entries.async_update_entry(entry, options={"scan_interval": 60})
        await hass.async_block_till_done()

    # Still loaded after the reload, and the new options are applied.
    assert entry.state is ConfigEntryState.LOADED
    assert entry.options == {"scan_interval": 60}


# --------------------------------------------------------------------------- #
# pure helpers
# --------------------------------------------------------------------------- #


def test_parse_service_time_valid() -> None:
    """A well-formed HH:MM string parses into (hour, minute)."""
    assert _parse_service_time("08:30") == (8, 30)
    assert _parse_service_time("23:59") == (23, 59)
    assert _parse_service_time("00:00") == (0, 0)


@pytest.mark.parametrize("bad", ["25:00", "08:60", "notatime", "12", "-1:00"])
def test_parse_service_time_invalid(bad: str) -> None:
    """Out-of-range or malformed times raise ServiceValidationError."""
    with pytest.raises(ServiceValidationError):
        _parse_service_time(bad)


def test_service_schedule_to_fluidra_valid() -> None:
    """A valid schedule is converted to the Fluidra CRON shape."""
    schedule = {
        "enabled": True,
        "start_time": "08:00",
        "end_time": "12:30",
        "mode": "2",
        "days": [3, 1, 2, 1],  # unsorted + duplicate -> sorted/deduped
    }
    result = _service_schedule_to_fluidra(schedule, schedule_id=4)

    assert result["id"] == "schedule_4"
    assert result["enabled"] is True
    assert result["startTime"] == "00 08 * * 1,2,3"
    assert result["endTime"] == "30 12 * * 1,2,3"
    assert result["startActions"]["operationName"] == "2"
    assert result["startActions"]["componentToChange"] == 11
    assert result["endActions"]["componentToChange"] == 9
    assert result["state"] == "IDLE"


def test_service_schedule_to_fluidra_invalid_time() -> None:
    """An out-of-range start_time ('25:00') bubbles up as ServiceValidationError."""
    schedule = {
        "enabled": True,
        "start_time": "25:00",
        "end_time": "12:00",
        "mode": "1",
        "days": [1],
    }
    with pytest.raises(ServiceValidationError):
        _service_schedule_to_fluidra(schedule, schedule_id=1)


def test_service_schedule_to_fluidra_empty_days() -> None:
    """An empty days list raises ServiceValidationError (empty_schedule_days)."""
    schedule = {
        "enabled": True,
        "start_time": "08:00",
        "end_time": "12:00",
        "mode": "1",
        "days": [],
    }
    with pytest.raises(ServiceValidationError):
        _service_schedule_to_fluidra(schedule, schedule_id=1)


# --------------------------------------------------------------------------- #
# service error / rejection paths
# --------------------------------------------------------------------------- #


async def test_service_set_schedule_api_error(hass: HomeAssistant, mock_api: AsyncMock) -> None:
    """A FluidraError from api.set_schedule surfaces as HomeAssistantError."""
    _prepare_api(mock_api)
    await _setup(hass, mock_api)
    mock_api.set_schedule = AsyncMock(side_effect=FluidraError("nope"))

    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(
            DOMAIN,
            "set_schedule",
            {
                "device_id": DEVICE_ID,
                "schedules": [{"enabled": True, "start_time": "08:00", "end_time": "12:00", "mode": "1"}],
            },
            blocking=True,
            return_response=True,
        )


async def test_service_set_schedule_rejected(hass: HomeAssistant, mock_api: AsyncMock) -> None:
    """api.set_schedule returning False raises HomeAssistantError (rejected)."""
    _prepare_api(mock_api)
    await _setup(hass, mock_api)
    mock_api.set_schedule = AsyncMock(return_value=False)

    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(
            DOMAIN,
            "set_schedule",
            {
                "device_id": DEVICE_ID,
                "schedules": [{"enabled": True, "start_time": "08:00", "end_time": "12:00", "mode": "1"}],
            },
            blocking=True,
            return_response=True,
        )


async def test_service_clear_schedule_api_error(hass: HomeAssistant, mock_api: AsyncMock) -> None:
    """A FluidraError from api.clear_schedule surfaces as HomeAssistantError."""
    _prepare_api(mock_api)
    await _setup(hass, mock_api)
    mock_api.clear_schedule = AsyncMock(side_effect=FluidraError("nope"))

    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(
            DOMAIN, "clear_schedule", {"device_id": DEVICE_ID}, blocking=True, return_response=True
        )


async def test_service_clear_schedule_rejected(hass: HomeAssistant, mock_api: AsyncMock) -> None:
    """api.clear_schedule returning False raises HomeAssistantError (rejected)."""
    _prepare_api(mock_api)
    await _setup(hass, mock_api)
    mock_api.clear_schedule = AsyncMock(return_value=False)

    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(
            DOMAIN, "clear_schedule", {"device_id": DEVICE_ID}, blocking=True, return_response=True
        )


async def test_service_preset_api_error(hass: HomeAssistant, mock_api: AsyncMock) -> None:
    """A FluidraError during a preset set surfaces as HomeAssistantError."""
    _prepare_api(mock_api)
    await _setup(hass, mock_api)
    mock_api.set_schedule = AsyncMock(side_effect=FluidraError("nope"))

    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(
            DOMAIN,
            "set_preset_schedule",
            {"device_id": DEVICE_ID, "preset": "eco"},
            blocking=True,
            return_response=True,
        )


async def test_service_preset_rejected(hass: HomeAssistant, mock_api: AsyncMock) -> None:
    """api.set_schedule returning False during a preset raises HomeAssistantError."""
    _prepare_api(mock_api)
    await _setup(hass, mock_api)
    mock_api.set_schedule = AsyncMock(return_value=False)

    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(
            DOMAIN,
            "set_preset_schedule",
            {"device_id": DEVICE_ID, "preset": "winter"},
            blocking=True,
            return_response=True,
        )


async def test_service_unknown_device(hass: HomeAssistant, mock_api: AsyncMock) -> None:
    """Targeting an unknown device with multiple coordinators is rejected.

    A single loaded entry falls back to its only coordinator, so to force the
    device_not_found path we set up two entries.
    """
    _prepare_api(mock_api)
    await _setup(hass, mock_api)

    # Second entry/coordinator so the "exactly one coordinator" shortcut is off.
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    api2 = mock_api
    entry2 = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_EMAIL: "d@e.f", CONF_PASSWORD: "y"},
        unique_id="d@e.f",
    )
    entry2.add_to_hass(hass)
    with patch(PATCH_TARGET, return_value=api2):
        await hass.config_entries.async_setup(entry2.entry_id)
        await hass.async_block_till_done()

    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN, "clear_schedule", {"device_id": "NOPE-999"}, blocking=True, return_response=True
        )


async def test_service_single_coordinator_fallback(hass: HomeAssistant, mock_api: AsyncMock) -> None:
    """With one loaded entry, an unknown device still resolves to that coordinator."""
    _prepare_api(mock_api)
    entry = await _setup(hass, mock_api)
    coordinator = entry.runtime_data.coordinator

    # device_id not present in coordinator.data -> single-coordinator fallback.
    resolved = _get_coordinator_for_device(hass, "UNKNOWN-DEVICE")
    assert resolved is coordinator


# --------------------------------------------------------------------------- #
# internal coordinator/device helpers
# --------------------------------------------------------------------------- #


async def test_get_device_data_and_schedule_component(hass: HomeAssistant, mock_api: AsyncMock) -> None:
    """_get_device_data finds the device and _get_schedule_component returns its slot."""
    _prepare_api(mock_api)
    entry = await _setup(hass, mock_api)
    coordinator = entry.runtime_data.coordinator

    found = _get_device_data(coordinator, DEVICE_ID)
    assert found is not None
    assert found["device_id"] == DEVICE_ID

    assert _get_device_data(coordinator, "MISSING") is None

    # Pump exposes a schedule_component feature (an int slot).
    assert isinstance(_get_schedule_component(coordinator, DEVICE_ID), int)


def test_get_device_data_no_coordinator_data() -> None:
    """_get_device_data returns None when the coordinator has no data."""
    from unittest.mock import MagicMock

    coordinator = MagicMock()
    coordinator.data = None
    assert _get_device_data(coordinator, DEVICE_ID) is None


def test_get_schedule_component_default_for_missing_device() -> None:
    """_get_schedule_component falls back to COMPONENT_SCHEDULE for an unknown device."""
    from unittest.mock import MagicMock

    from custom_components.fluidra_pool.const import COMPONENT_SCHEDULE

    coordinator = MagicMock()
    coordinator.data = {}
    assert _get_schedule_component(coordinator, "MISSING") == COMPONENT_SCHEDULE


# --------------------------------------------------------------------------- #
# migration
# --------------------------------------------------------------------------- #


async def test_migrate_entry_version_1(hass: HomeAssistant) -> None:
    """The current version (1) needs no migration and returns True."""
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    entry = MockConfigEntry(domain=DOMAIN, data={}, version=1)
    entry.add_to_hass(hass)
    assert await async_migrate_entry(hass, entry) is True


async def test_migrate_entry_future_version_fails(hass: HomeAssistant) -> None:
    """A version higher than expected cannot be migrated (returns False)."""
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    entry = MockConfigEntry(domain=DOMAIN, data={}, version=2)
    entry.add_to_hass(hass)
    assert await async_migrate_entry(hass, entry) is False


# --------------------------------------------------------------------------- #
# options listener no-op (data-only change)
# --------------------------------------------------------------------------- #


async def test_options_unchanged_no_reload(hass: HomeAssistant, mock_api: AsyncMock) -> None:
    """A data-only update (refresh_token persist) must NOT reload the entry.

    Updating only entry.data leaves options equal to the snapshot, so the
    listener short-circuits and the same coordinator instance survives.
    """
    _prepare_api(mock_api)
    entry = await _setup(hass, mock_api)
    coordinator_before = entry.runtime_data.coordinator

    hass.config_entries.async_update_entry(entry, data={**entry.data, "refresh_token": "tok-123"})
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.LOADED
    # No reload happened: the runtime_data/coordinator object is unchanged.
    assert entry.runtime_data.coordinator is coordinator_before


# --------------------------------------------------------------------------- #
# refresh-token persist callback + service registration idempotency
# --------------------------------------------------------------------------- #


async def test_persist_refresh_token_callback(hass: HomeAssistant, mock_api: AsyncMock) -> None:
    """The on_token_persist callback writes a new token but no-ops on an identical one."""
    _prepare_api(mock_api)

    from pytest_homeassistant_custom_component.common import MockConfigEntry

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_EMAIL: "a@b.c", CONF_PASSWORD: "x"},
        unique_id="a@b.c",
    )
    entry.add_to_hass(hass)

    captured: dict = {}

    def _factory(*args, **kwargs):
        captured["on_token_persist"] = kwargs.get("on_token_persist")
        return mock_api

    with patch(PATCH_TARGET, side_effect=_factory):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    persist = captured["on_token_persist"]
    assert persist is not None

    # New token -> entry.data updated.
    persist("brand-new-token")
    await hass.async_block_till_done()
    assert entry.data["refresh_token"] == "brand-new-token"

    # Same token again -> no-op (still equal, branch covered).
    persist("brand-new-token")
    await hass.async_block_till_done()
    assert entry.data["refresh_token"] == "brand-new-token"


async def test_register_services_idempotent(hass: HomeAssistant, mock_api: AsyncMock) -> None:
    """Calling _async_register_services twice is a no-op the second time."""
    _prepare_api(mock_api)
    await _setup(hass, mock_api)

    # Services already registered by setup; a second registration short-circuits.
    assert hass.services.has_service(DOMAIN, "set_schedule")
    await _async_register_services(hass)
    assert hass.services.has_service(DOMAIN, "set_schedule")
