"""Tests for the bulk component fetch (Issue #144).

One request per device replaces the per-component fan-out. The bulk path is
optional: anything unexpected falls back to per-component reads, and repeated
failures disable it for the session so an unsupported backend can't double the
request count forever.
"""

from __future__ import annotations

import logging
from typing import Any
from unittest.mock import AsyncMock

from homeassistant.core import HomeAssistant
import pytest

from custom_components.fluidra_pool.const import BULK_FETCH_FAILURE_LIMIT
from custom_components.fluidra_pool.coordinator import FluidraDataUpdateCoordinator
from custom_components.fluidra_pool.fluidra_api._components import ComponentsMixin


@pytest.fixture
def coordinator(hass: HomeAssistant, mock_api: AsyncMock) -> FluidraDataUpdateCoordinator:
    return FluidraDataUpdateCoordinator(hass, mock_api)


# --- payload parsing ------------------------------------------------------


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        # A bare list of component objects (the shape the app's endpoint returns).
        ([{"id": 14, "reportedValue": "RUNNING"}, {"id": 21, "reportedValue": 75}], {14, 21}),
        # Wrapped in a "components" key.
        ({"components": [{"id": 9, "reportedValue": 1}]}, {9}),
        # Already an id-keyed mapping.
        ({"9": {"reportedValue": 1}, "10": {"reportedValue": 0}}, {9, 10}),
    ],
)
def test_parse_bulk_components_accepts_known_shapes(payload: Any, expected: set[int]) -> None:
    parsed = ComponentsMixin._parse_bulk_components(payload)
    assert parsed is not None
    assert set(parsed) == expected


@pytest.mark.parametrize("payload", [None, "nonsense", 42, [], {}, [{"no_id": 1}], {"components": "bad"}])
def test_parse_bulk_components_rejects_unusable_payloads(payload: Any) -> None:
    """Unrecognised or empty payloads return None so the caller falls back.

    Returning {} instead would look like "this device has no components" and
    silently blank every entity.
    """
    assert ComponentsMixin._parse_bulk_components(payload) is None


def test_parse_bulk_components_skips_malformed_entries() -> None:
    """A bad entry is dropped without losing the good ones."""
    parsed = ComponentsMixin._parse_bulk_components(
        [{"id": 14, "reportedValue": "RUNNING"}, {"id": "oops"}, "junk", {"id": 21, "reportedValue": 1}]
    )
    assert parsed is not None
    assert set(parsed) == {14, 21}


def test_parse_bulk_components_mapping_skips_bad_keys_and_values() -> None:
    """In an id-keyed mapping, non-numeric keys and non-dict values are dropped."""
    parsed = ComponentsMixin._parse_bulk_components(
        {"14": {"reportedValue": "RUNNING"}, "notanumber": {"reportedValue": 1}, "21": "not-a-dict"}
    )
    assert parsed is not None
    assert set(parsed) == {14}


# --- coordinator integration ---------------------------------------------


async def test_bulk_fetch_used_and_filtered_to_requested(
    coordinator: FluidraDataUpdateCoordinator, mock_api: AsyncMock
) -> None:
    """The bulk result is used, restricted to the profile's requested components."""
    mock_api.get_all_components = AsyncMock(
        return_value={
            9: {"reportedValue": 1},
            14: {"reportedValue": "RUNNING"},
            99: {"reportedValue": "not requested"},
        }
    )
    states = await coordinator._fetch_components("DEV-1", [9, 14])
    assert set(states) == {9, 14}  # c99 dropped — the profile didn't ask for it
    mock_api.get_component_state.assert_not_awaited()  # no per-component fan-out


async def test_bulk_failure_falls_back_to_per_component(
    coordinator: FluidraDataUpdateCoordinator, mock_api: AsyncMock
) -> None:
    """A failed bulk read still returns data via the per-component path."""
    mock_api.get_all_components = AsyncMock(return_value=None)
    mock_api.get_component_state = AsyncMock(return_value={"reportedValue": 7})
    states = await coordinator._fetch_components("DEV-1", [9, 10])
    assert set(states) == {9, 10}
    assert mock_api.get_component_state.await_count == 2


async def test_non_dict_bulk_result_falls_back(coordinator: FluidraDataUpdateCoordinator, mock_api: AsyncMock) -> None:
    """A bulk return that isn't an id-keyed mapping is treated as a failure."""
    mock_api.get_all_components = AsyncMock(return_value=["unexpected"])
    mock_api.get_component_state = AsyncMock(return_value={"reportedValue": 1})
    states = await coordinator._fetch_components("DEV-1", [9])
    assert set(states) == {9}
    assert mock_api.get_component_state.await_count == 1


async def test_bulk_disabled_per_device_after_repeated_failures(
    coordinator: FluidraDataUpdateCoordinator, mock_api: AsyncMock
) -> None:
    """After the failure limit the bulk endpoint is no longer attempted for THAT device."""
    mock_api.get_all_components = AsyncMock(return_value=None)
    mock_api.get_component_state = AsyncMock(return_value={"reportedValue": 1})

    for _ in range(BULK_FETCH_FAILURE_LIMIT):
        await coordinator._fetch_components("DEV-1", [9])

    assert coordinator._bulk_fetch_failures["DEV-1"] == BULK_FETCH_FAILURE_LIMIT
    calls_so_far = mock_api.get_all_components.await_count
    await coordinator._fetch_components("DEV-1", [9])
    # No further bulk attempts for it, but data still flows through the fallback.
    assert mock_api.get_all_components.await_count == calls_so_far


async def test_bulk_giving_up_on_one_device_does_not_affect_others(
    coordinator: FluidraDataUpdateCoordinator, mock_api: AsyncMock
) -> None:
    """A device that 404s the bulk endpoint must not disable it for the rest.

    Issue #175: a virtual `standardTestStrip` device answers 404 while the real
    hardware on the same account answers fine.
    """

    async def _bulk(device_id: str) -> dict | None:
        return None if device_id == "STRIP" else {9: {"reportedValue": 1}}

    mock_api.get_all_components = AsyncMock(side_effect=_bulk)
    mock_api.get_component_state = AsyncMock(return_value={"reportedValue": 1})

    for _ in range(BULK_FETCH_FAILURE_LIMIT + 2):
        await coordinator._fetch_components("STRIP", [9])
        await coordinator._fetch_components("REAL", [9])

    # The healthy device keeps using the bulk path...
    assert "REAL" not in coordinator._bulk_fetch_failures
    # ...while the 404-ing one is capped and stops being retried.
    assert coordinator._bulk_fetch_failures["STRIP"] == BULK_FETCH_FAILURE_LIMIT


async def test_healthy_device_success_does_not_reset_another_devices_streak(
    coordinator: FluidraDataUpdateCoordinator, mock_api: AsyncMock
) -> None:
    """The regression itself: a shared counter was zeroed by other devices' successes,
    so the unsupported device was retried on every poll forever (Issue #175)."""

    async def _bulk(device_id: str) -> dict | None:
        return None if device_id == "STRIP" else {9: {"reportedValue": 1}}

    mock_api.get_all_components = AsyncMock(side_effect=_bulk)
    mock_api.get_component_state = AsyncMock(return_value={"reportedValue": 1})

    await coordinator._fetch_components("STRIP", [9])
    await coordinator._fetch_components("REAL", [9])  # would previously reset the count
    assert coordinator._bulk_fetch_failures["STRIP"] == 1


async def test_bulk_success_resets_that_devices_failure_streak(
    coordinator: FluidraDataUpdateCoordinator, mock_api: AsyncMock
) -> None:
    """Transient failures must not accumulate towards giving up on the bulk path."""
    mock_api.get_all_components = AsyncMock(return_value=None)
    await coordinator._fetch_components("DEV-1", [9])
    assert coordinator._bulk_fetch_failures["DEV-1"] == 1

    mock_api.get_all_components = AsyncMock(return_value={9: {"reportedValue": 1}})
    await coordinator._fetch_components("DEV-1", [9])
    assert "DEV-1" not in coordinator._bulk_fetch_failures


# --- pool schedulers fetched only when a device needs them (Issue #144) ---


async def _refresh_with_device(coordinator: FluidraDataUpdateCoordinator, mock_api: AsyncMock, device: dict) -> dict:
    mock_api.get_pool_details = AsyncMock(return_value={})
    mock_api.poll_water_quality = AsyncMock(return_value=None)
    mock_api.poll_pool_device_statuses = AsyncMock(return_value=None)
    pool: dict[str, Any] = {"id": "pool_1", "name": "Pool", "devices": [device]}
    await coordinator._refresh_pool(pool, {})
    return pool


def _victoria_device() -> dict[str, Any]:
    """Device pinned to a profile that declares uses_pool_schedulers."""
    from types import SimpleNamespace

    return {
        "device_id": "VIC-1",
        "name": "Victoria Smart Connect VS",
        "family": "",
        "type": "pump",
        "model": "",
        "online": True,
        "components": {},
        "_identify_cache": {
            "key": ("VIC-1", "", "", "pump", ""),
            "config": SimpleNamespace(
                device_type="pump",
                features={"uses_pool_schedulers": True, "specific_components": [14]},
                components_range=25,
                required_components=[0, 1, 2, 3],
                entities=[],
                verified=True,
            ),
        },
    }


async def test_schedulers_fetched_and_stored_for_pumps_that_use_them(
    coordinator: FluidraDataUpdateCoordinator, mock_api: AsyncMock
) -> None:
    entries = [{"id": "s0", "name": "Filtration"}]
    mock_api.get_pool_schedulers = AsyncMock(return_value=entries)
    pool = await _refresh_with_device(coordinator, mock_api, _victoria_device())
    mock_api.get_pool_schedulers.assert_awaited_once_with("pool_1")
    assert pool["schedulers"] == entries


async def test_schedulers_not_fetched_for_other_devices(
    coordinator: FluidraDataUpdateCoordinator, mock_api: AsyncMock
) -> None:
    """Pools without schedule-driven equipment must not pay an extra request."""
    from types import SimpleNamespace

    plain = _victoria_device()
    plain["_identify_cache"]["config"] = SimpleNamespace(
        device_type="pump",
        features={"specific_components": [9]},
        components_range=25,
        required_components=[0, 1, 2, 3],
        entities=[],
        verified=True,
    )
    mock_api.get_pool_schedulers = AsyncMock(return_value=[{"id": "s0"}])
    pool = await _refresh_with_device(coordinator, mock_api, plain)
    mock_api.get_pool_schedulers.assert_not_awaited()
    assert "schedulers" not in pool


async def test_scheduler_fetch_failure_leaves_pool_usable(
    coordinator: FluidraDataUpdateCoordinator, mock_api: AsyncMock
) -> None:
    """A failed scheduler read degrades quietly — the rest of the poll still works."""
    mock_api.get_pool_schedulers = AsyncMock(return_value=None)
    pool = await _refresh_with_device(coordinator, mock_api, _victoria_device())
    assert "schedulers" not in pool


# --- unmapped-register discovery aid (Issues #174/#175) -------------------


async def test_unmapped_components_logged_once_at_debug(
    coordinator: FluidraDataUpdateCoordinator, mock_api: AsyncMock, caplog: pytest.LogCaptureFixture
) -> None:
    """Registers the device reports but no profile maps are dumped at DEBUG.

    Users asking for a missing feature (boost, aux, schedules) can enable debug
    logging, toggle it in the app, and read off which register moved — instead of
    sending diagnostics that only ever contain the already-known registers.
    """
    mock_api.get_all_components = AsyncMock(
        return_value={9: {"reportedValue": 1}, 77: {"reportedValue": True}, 88: {"reportedValue": "AUTO"}}
    )
    with caplog.at_level(logging.DEBUG, logger="custom_components.fluidra_pool.coordinator.coordinator"):
        await coordinator._fetch_components("DEV-1", [9])
        await coordinator._fetch_components("DEV-1", [9])  # second poll must stay quiet

    dumps = [r for r in caplog.records if "no profile maps" in r.getMessage()]
    assert len(dumps) == 1, "should log once per device per session, not every poll"
    message = dumps[0].getMessage()
    assert "77" in message  # the unmapped ones
    assert "88" in message
    assert "'reportedValue'" not in message  # values, not raw wrappers


async def test_unmapped_logging_skipped_when_everything_is_mapped(
    coordinator: FluidraDataUpdateCoordinator, mock_api: AsyncMock, caplog: pytest.LogCaptureFixture
) -> None:
    """Nothing to report when the profile already covers every register."""
    mock_api.get_all_components = AsyncMock(return_value={9: {"reportedValue": 1}})
    with caplog.at_level(logging.DEBUG, logger="custom_components.fluidra_pool.coordinator.coordinator"):
        await coordinator._fetch_components("DEV-1", [9])
    assert not [r for r in caplog.records if "no profile maps" in r.getMessage()]


async def test_unmapped_logging_tracked_per_device(
    coordinator: FluidraDataUpdateCoordinator, mock_api: AsyncMock, caplog: pytest.LogCaptureFixture
) -> None:
    """Each device gets its own one-shot dump."""
    mock_api.get_all_components = AsyncMock(return_value={9: {"reportedValue": 1}, 77: {"reportedValue": 2}})
    with caplog.at_level(logging.DEBUG, logger="custom_components.fluidra_pool.coordinator.coordinator"):
        await coordinator._fetch_components("DEV-1", [9])
        await coordinator._fetch_components("DEV-2", [9])
    assert len([r for r in caplog.records if "no profile maps" in r.getMessage()]) == 2
