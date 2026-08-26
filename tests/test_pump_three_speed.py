"""Tests for the three-speed pump select (plan 013, Pass 2).

The controller carries the speed on a single register it also accepts writes
on, unlike the E30iQ, which is driven through its own c9/c11 pair by
:class:`FluidraPumpSpeedSelect`.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
import pytest

from custom_components.fluidra_pool.api_resilience import FluidraError
from custom_components.fluidra_pool.select import (
    FluidraPumpSpeedSelect,
    FluidraThreeSpeedPumpSelect,
    async_setup_entry,
)

POOL_ID = "pool-1"
DEVICE_ID = "TEST-PUMP-001"
COMPONENT = 137


def _device(
    components: dict | None = None,
    features: dict | None = None,
    device_id: str = DEVICE_ID,
    **extra: Any,
) -> dict:
    device = {
        "device_id": device_id,
        "name": "Filtration pump",
        "family": "Pumps",
        "type": "pump",
        "model": "Pump",
        "online": True,
        "components": components if components is not None else {},
        "_identify_cache": {
            "key": (device_id, "Pumps", "Pump", "pump", ""),
            "config": SimpleNamespace(
                device_type="pump",
                features=features or {},
                components_range=25,
                required_components=[0, 1, 2, 3],
                entities=["select", "sensor_info"],
            ),
        },
    }
    device.update(extra)
    return device


def _coord(devices: list[dict], access_level: str | None = None) -> Any:
    pool: dict[str, Any] = {"id": POOL_ID, "name": "Pool", "devices": devices}
    if access_level is not None:
        pool["access_level"] = access_level
    coordinator = MagicMock()
    coordinator.data = {POOL_ID: pool}
    coordinator.last_update_success = True
    coordinator.async_request_refresh = AsyncMock()
    return coordinator


def _select(
    components: dict,
    *,
    write_map: dict | None = None,
    read_map: dict | None = None,
    api: Any = None,
    access_level: str | None = None,
) -> FluidraThreeSpeedPumpSelect:
    entity = FluidraThreeSpeedPumpSelect(
        _coord([_device(components)], access_level),
        api or SimpleNamespace(),
        POOL_ID,
        DEVICE_ID,
        COMPONENT,
        write_map,
        read_map,
    )
    entity.async_write_ha_state = MagicMock()
    return entity


# --- Reading the speed -------------------------------------------------------


def test_options_come_from_the_write_table() -> None:
    """The offered options are exactly the speeds the profile can write."""
    assert _select({}).options == ["low", "medium", "high"]


@pytest.mark.parametrize(("reported", "expected"), [(0, "low"), (1, "medium"), (2, "high")])
def test_current_option_uses_the_read_table(reported: int, expected: str) -> None:
    """The reported integer is translated through the profile's read table."""
    assert _select({"137": {"reportedValue": reported}}).current_option == expected


def test_current_option_none_when_register_absent() -> None:
    """Nothing reported means unknown, not a default speed."""
    assert _select({}).current_option is None


def test_current_option_none_on_value_outside_the_table() -> None:
    """An unmapped value means the profile's table is wrong for this unit -- say nothing."""
    assert _select({"137": {"reportedValue": 7}}).current_option is None


def test_current_option_none_on_unparsable_value() -> None:
    """Unparsable readings degrade to unknown."""
    assert _select({"137": {"reportedValue": "n/a"}}).current_option is None


def test_read_and_write_tables_are_profile_overridable() -> None:
    """A family numbering its speeds differently is a profile edit, not a code change."""
    entity = _select(
        {"137": {"reportedValue": 3}},
        write_map={"eco": 5, "boost": 6},
        read_map={3: "eco", 4: "boost"},
    )
    assert entity.options == ["eco", "boost"]
    assert entity.current_option == "eco"


# --- Writing the speed -------------------------------------------------------


async def test_select_option_writes_the_mapped_value() -> None:
    """Picking a speed writes the profile's value on the declared register."""
    api = SimpleNamespace(control_device_component=AsyncMock(return_value=True))
    entity = _select({"137": {"reportedValue": 0}}, api=api)

    await entity.async_select_option("high")

    api.control_device_component.assert_awaited_once_with(DEVICE_ID, COMPONENT, 3)
    entity.coordinator.async_request_refresh.assert_awaited_once()


async def test_unknown_option_is_ignored() -> None:
    """An option outside the table never reaches the network."""
    api = SimpleNamespace(control_device_component=AsyncMock(return_value=True))
    entity = _select({}, api=api)

    await entity.async_select_option("turbo")

    api.control_device_component.assert_not_awaited()


async def test_refused_write_raises_and_clears_the_optimistic_state() -> None:
    """A write the cloud refuses must not leave the UI showing the new speed."""
    api = SimpleNamespace(control_device_component=AsyncMock(return_value=False))
    entity = _select({"137": {"reportedValue": 0}}, api=api)

    with pytest.raises(HomeAssistantError):
        await entity.async_select_option("medium")

    assert entity.current_option == "low"


async def test_transport_error_raises_and_clears_the_optimistic_state() -> None:
    """Same for a transport failure."""
    api = SimpleNamespace(control_device_component=AsyncMock(side_effect=FluidraError("boom")))
    entity = _select({"137": {"reportedValue": 0}}, api=api)

    with pytest.raises(HomeAssistantError):
        await entity.async_select_option("medium")

    assert entity.current_option == "low"


async def test_viewer_account_cannot_write() -> None:
    """The read-only guard fires before any optimistic state is set (Issue #133)."""
    api = SimpleNamespace(control_device_component=AsyncMock(return_value=True))
    entity = _select({"137": {"reportedValue": 0}}, api=api, access_level="viewer")

    with pytest.raises(ServiceValidationError):
        await entity.async_select_option("high")

    api.control_device_component.assert_not_awaited()


# --- Platform wiring ---------------------------------------------------------


async def _run_setup(devices: list[dict]) -> list[Any]:
    pool = {"id": POOL_ID, "name": "Pool", "devices": devices}
    coordinator = MagicMock()
    coordinator.data = {POOL_ID: pool}
    coordinator.last_update_success = True
    coordinator.api = SimpleNamespace(cached_pools=[pool], get_pools=AsyncMock(return_value=[pool]))
    coordinator.get_pools_from_data = lambda: [{"id": POOL_ID, **coordinator.data[POOL_ID]}]
    coordinator.async_add_listener = lambda cb: lambda: None

    added: list[Any] = []
    entry = SimpleNamespace(
        runtime_data=SimpleNamespace(coordinator=coordinator),
        async_on_unload=lambda _unsub: None,
    )
    async_add = MagicMock(side_effect=lambda ents, *a, **k: added.extend(list(ents)))
    await async_setup_entry(MagicMock(), entry, async_add)
    return added


async def test_three_speed_select_created_only_when_declared() -> None:
    """No profile feature, no entity."""
    declared = _device(
        {"137": {"reportedValue": 1}},
        {"pump_3speed": {"component": COMPONENT}},
        device_id="dev1",
        variable_speed=True,
    )
    plain = _device({}, {}, device_id="dev2", variable_speed=True)
    added = await _run_setup([declared, plain])

    uids = {e.unique_id for e in added}
    assert "fluidra_pool_pool-1_dev1_pump_3speed" in uids
    assert "fluidra_pool_pool-1_dev2_pump_3speed" not in uids


async def test_three_speed_replaces_the_e30iq_speed_select() -> None:
    """The two speed controls are mutually exclusive on the same pump."""
    declared = _device(
        {"137": {"reportedValue": 1}},
        {"pump_3speed": {"component": COMPONENT}},
        device_id="dev1",
        variable_speed=True,
    )
    added = await _run_setup([declared])

    assert not any(isinstance(e, FluidraPumpSpeedSelect) for e in added)
    assert any(isinstance(e, FluidraThreeSpeedPumpSelect) for e in added)


async def test_plain_variable_speed_pump_keeps_its_select() -> None:
    """An E30iQ is untouched by this pass."""
    added = await _run_setup([_device({}, {}, device_id="dev2", variable_speed=True)])

    assert any(isinstance(e, FluidraPumpSpeedSelect) for e in added)
