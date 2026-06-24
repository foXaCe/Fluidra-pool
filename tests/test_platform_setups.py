"""Tests for the per-platform ``async_setup_entry`` functions.

These exercise the entity-selection branches of the switch/select/number/time/
light platforms by building pinned device dicts (via ``_identify_cache``) so
``DeviceIdentifier`` returns a controlled :class:`DeviceConfig`. The platform
setups are called directly with a fake config entry to keep the tests fast and
independent of a running Home Assistant instance.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from custom_components.fluidra_pool.light import (
    FluidraLight,
)
from custom_components.fluidra_pool.light import (
    async_setup_entry as light_setup,
)
from custom_components.fluidra_pool.number import (
    FluidraChlorinatorLevelNumber,
    FluidraChlorinatorOrpSetpoint,
    FluidraChlorinatorPhSetpoint,
    FluidraLightEffectSpeed,
)
from custom_components.fluidra_pool.number import (
    async_setup_entry as number_setup,
)
from custom_components.fluidra_pool.select import (
    FluidraChlorinatorModeSelect,
    FluidraChlorinatorScheduleSpeedSelect,
    FluidraLightEffectSelect,
    FluidraPumpSpeedSelect,
    FluidraScheduleModeSelect,
)
from custom_components.fluidra_pool.select import (
    async_setup_entry as select_setup,
)
from custom_components.fluidra_pool.switch import (
    FluidraAutoModeSwitch,
    FluidraChlorinatorBoostSwitch,
    FluidraChlorinatorSwitch,
    FluidraHeatPumpSwitch,
    FluidraPumpSwitch,
    FluidraScheduleEnableSwitch,
)
from custom_components.fluidra_pool.switch import (
    async_setup_entry as switch_setup,
)
from custom_components.fluidra_pool.switch.heater import FluidraHeaterSwitch
from custom_components.fluidra_pool.time import (
    FluidraLightScheduleEndTimeEntity,
    FluidraLightScheduleStartTimeEntity,
    FluidraScheduleEndTimeEntity,
    FluidraScheduleStartTimeEntity,
)
from custom_components.fluidra_pool.time import (
    async_setup_entry as time_setup,
)

POOL_ID = "pool-1"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _pin(device: dict, *, device_type: str, entities=None, features=None) -> dict:
    """Pin ``identify_device`` for a device dict via its ``_identify_cache``.

    The cache key must match exactly: (device_id, family, model, type, comp7).
    """
    components = device.get("components", {})
    comp7 = ""
    if "7" in components and isinstance(components["7"], dict):
        comp7 = str(components["7"].get("reportedValue", ""))
    device["_identify_cache"] = {
        "key": (
            device.get("device_id", ""),
            device.get("family", ""),
            device.get("model", ""),
            device.get("type", ""),
            comp7,
        ),
        "config": SimpleNamespace(
            device_type=device_type,
            features=features or {},
            entities=entities if entities is not None else [],
            components_range=25,
            required_components=[0, 1, 2, 3],
        ),
    }
    return device


def _device(device_id: str, **extra) -> dict:
    """Build a base device dict (overridable fields via kwargs)."""
    device = {
        "device_id": device_id,
        "name": f"Device {device_id}",
        "family": "",
        "model": "",
        "type": "",
        "online": True,
        "components": {},
    }
    device.update(extra)
    return device


def _coordinator(devices, *, data=None):
    """Build a MagicMock coordinator with cached pools + api wired up."""
    coordinator = MagicMock()
    pool = {"id": POOL_ID, "name": "Pool", "devices": devices}
    coordinator.data = data if data is not None else {POOL_ID: pool}
    coordinator.last_update_success = True
    coordinator.async_request_refresh = AsyncMock()
    coordinator.async_config_entry_first_refresh = AsyncMock()
    api = SimpleNamespace(
        cached_pools=[pool],
        get_pools=AsyncMock(return_value=[pool]),
    )
    coordinator.api = api
    return coordinator


def _entry(coordinator):
    return SimpleNamespace(
        runtime_data=SimpleNamespace(coordinator=coordinator),
        async_on_unload=lambda _unsub: None,
    )


async def _run(setup, coordinator):
    """Call a platform's async_setup_entry and return the list of added entities."""
    added: list = []

    def _add(entities, *a, **k):
        added.extend(list(entities))

    async_add = MagicMock(side_effect=_add)
    await setup(MagicMock(), _entry(coordinator), async_add)
    return added, async_add


def _count(entities, cls) -> int:
    return sum(1 for e in entities if isinstance(e, cls))


# ---------------------------------------------------------------------------
# SWITCH platform
# ---------------------------------------------------------------------------
async def test_switch_setup_full_mix():
    """A pump, heat pump, heater and full-featured chlorinator fire many branches."""
    pump = _pin(
        _device("PUMP-1"),
        device_type="pump",
        entities=["switch", "switch_auto", "select"],
        features={"schedules": True, "schedule_count": 3},
    )
    heat = _pin(_device("HEAT-1"), device_type="heat_pump", entities=["switch"])
    heater = _pin(_device("HTR-1"), device_type="heater", entities=["switch"])
    chlor = _pin(
        _device("CHL-1"),
        device_type="chlorinator",
        entities=["switch"],
        features={"on_off_component": 3, "boost_mode": True},
    )

    coordinator = _coordinator([pump, heat, heater, chlor])
    added, async_add = await _run(switch_setup, coordinator)

    async_add.assert_called_once()
    assert _count(added, FluidraPumpSwitch) == 1
    assert _count(added, FluidraHeatPumpSwitch) == 1
    assert _count(added, FluidraHeaterSwitch) == 1
    assert _count(added, FluidraChlorinatorSwitch) == 1
    # switch_auto + not skip_auto_mode -> auto mode switch
    assert _count(added, FluidraAutoModeSwitch) == 1
    # schedules feature with schedule_count=3 -> 3 schedule enable switches
    assert _count(added, FluidraScheduleEnableSwitch) == 3
    # boost_mode feature -> boost switch
    assert _count(added, FluidraChlorinatorBoostSwitch) == 1


async def test_switch_setup_skips_device_without_id():
    """A device with no device_id is skipped entirely."""
    bad = _pin(_device("X"), device_type="pump", entities=["switch"])
    bad["device_id"] = ""
    coordinator = _coordinator([bad])
    added, _ = await _run(switch_setup, coordinator)
    assert added == []


async def test_switch_setup_chlorinator_without_on_off_no_main_switch():
    """A chlorinator without on_off_component gets no main switch entity."""
    chlor = _pin(
        _device("CHL-2"),
        device_type="chlorinator",
        entities=["switch"],
        features={},
    )
    coordinator = _coordinator([chlor])
    added, _ = await _run(switch_setup, coordinator)
    assert _count(added, FluidraChlorinatorSwitch) == 0


async def test_switch_setup_skip_auto_mode_suppresses_auto_switch():
    """skip_auto_mode feature suppresses the auto-mode switch even with switch_auto entity."""
    pump = _pin(
        _device("PUMP-2"),
        device_type="pump",
        entities=["switch", "switch_auto"],
        features={"skip_auto_mode": True},
    )
    coordinator = _coordinator([pump])
    added, _ = await _run(switch_setup, coordinator)
    assert _count(added, FluidraAutoModeSwitch) == 0
    assert _count(added, FluidraPumpSwitch) == 1


async def test_switch_setup_schedule_count_defaults_to_eight():
    """schedules feature without explicit count defaults to 8 schedule slots."""
    pump = _pin(
        _device("PUMP-3"),
        device_type="pump",
        entities=["switch"],
        features={"schedules": True},
    )
    coordinator = _coordinator([pump])
    added, _ = await _run(switch_setup, coordinator)
    assert _count(added, FluidraScheduleEnableSwitch) == 8


async def test_switch_setup_uses_get_pools_when_cache_empty():
    """When cached_pools is falsy, the setup falls back to get_pools()."""
    pump = _pin(_device("PUMP-4"), device_type="pump", entities=["switch"])
    coordinator = _coordinator([pump])
    coordinator.api.cached_pools = []
    added, _ = await _run(switch_setup, coordinator)
    coordinator.api.get_pools.assert_awaited_once()
    assert _count(added, FluidraPumpSwitch) == 1


# ---------------------------------------------------------------------------
# SELECT platform
# ---------------------------------------------------------------------------
async def test_select_setup_full_mix():
    """Chlorinator + pump + light selects all fire."""
    chlor = _pin(
        _device("CHL-S1"),
        device_type="chlorinator",
        entities=["select"],
        features={"schedule_component": 20, "schedule_count": 2},
    )
    pump = _pin(
        _device("PUMP-S1", variable_speed=True, schedule_data=[{"id": 1}]),
        device_type="pump",
        entities=["select"],
        features={},
    )
    light = _pin(
        _device("LT-S1"),
        device_type="light",
        entities=["select"],
        features={"effect_select": 21},
    )

    coordinator = _coordinator([chlor, pump, light])
    added, async_add = await _run(select_setup, coordinator)

    async_add.assert_called_once()
    assert _count(added, FluidraChlorinatorModeSelect) == 1
    assert _count(added, FluidraPumpSpeedSelect) == 1
    # pump with schedule_data -> 8 schedule mode selects
    assert _count(added, FluidraScheduleModeSelect) == 8
    assert _count(added, FluidraLightEffectSelect) == 1
    # chlorinator schedule_component with count=2 -> 2 schedule speed selects
    assert _count(added, FluidraChlorinatorScheduleSpeedSelect) == 2


async def test_select_setup_chlorinator_skip_mode_select():
    """skip_mode_select suppresses the chlorinator mode select."""
    chlor = _pin(
        _device("CHL-S2"),
        device_type="chlorinator",
        entities=["select"],
        features={"skip_mode_select": True},
    )
    coordinator = _coordinator([chlor])
    added, _ = await _run(select_setup, coordinator)
    assert _count(added, FluidraChlorinatorModeSelect) == 0


async def test_select_setup_skip_schedules_short_circuits():
    """skip_schedules continues past schedule/speed branches (heat pump style)."""
    hp = _pin(
        _device("HP-S1", variable_speed=True, schedule_data=[{"id": 1}]),
        device_type="pump",
        entities=["select"],
        features={"skip_schedules": True, "schedule_component": 20},
    )
    coordinator = _coordinator([hp])
    added, _ = await _run(select_setup, coordinator)
    # The continue means no speed/schedule selects are added.
    assert _count(added, FluidraPumpSpeedSelect) == 0
    assert _count(added, FluidraScheduleModeSelect) == 0


async def test_select_setup_pump_without_variable_speed_no_speed_select():
    """A pump that is not variable-speed gets no speed select."""
    pump = _pin(
        _device("PUMP-S3"),
        device_type="pump",
        entities=["select"],
        features={},
    )
    coordinator = _coordinator([pump])
    added, _ = await _run(select_setup, coordinator)
    assert _count(added, FluidraPumpSpeedSelect) == 0
    assert _count(added, FluidraScheduleModeSelect) == 0


async def test_select_setup_light_without_effect_component():
    """A light without effect_select feature gets no effect select."""
    light = _pin(
        _device("LT-S2"),
        device_type="light",
        entities=["select"],
        features={},
    )
    coordinator = _coordinator([light])
    added, _ = await _run(select_setup, coordinator)
    assert _count(added, FluidraLightEffectSelect) == 0


async def test_select_setup_skips_device_without_id():
    """A device without device_id is skipped."""
    chlor = _pin(_device("CHL-S9"), device_type="chlorinator", entities=["select"])
    chlor["device_id"] = ""
    coordinator = _coordinator([chlor])
    added, _ = await _run(select_setup, coordinator)
    assert added == []


async def test_select_setup_chlorinator_schedule_count_default_three():
    """schedule_component without explicit count defaults to 3 selects."""
    chlor = _pin(
        _device("CHL-S3"),
        device_type="chlorinator",
        entities=["select"],
        features={"schedule_component": 20, "skip_mode_select": True},
    )
    coordinator = _coordinator([chlor])
    added, _ = await _run(select_setup, coordinator)
    assert _count(added, FluidraChlorinatorScheduleSpeedSelect) == 3


# ---------------------------------------------------------------------------
# NUMBER platform
# ---------------------------------------------------------------------------
async def test_number_setup_chlorinator_with_setpoints():
    """Chlorinator with ph/orp setpoint features fires all three number entities."""
    chlor = _pin(
        _device("CHL-N1"),
        device_type="chlorinator",
        entities=["number"],
        features={
            "ph_setpoint": {"write": 8, "read": 172},
            "orp_setpoint": {"write": 11, "read": 177},
            "chlorination_level": 10,
        },
    )
    coordinator = _coordinator([chlor])
    added, async_add = await _run(number_setup, coordinator)

    async_add.assert_called_once()
    assert _count(added, FluidraChlorinatorLevelNumber) == 1
    assert _count(added, FluidraChlorinatorPhSetpoint) == 1
    assert _count(added, FluidraChlorinatorOrpSetpoint) == 1


async def test_number_setup_chlorinator_level_only():
    """Chlorinator with number entity but no setpoint features -> only level number."""
    chlor = _pin(
        _device("CHL-N2"),
        device_type="chlorinator",
        entities=["number"],
        features={},
    )
    coordinator = _coordinator([chlor])
    added, _ = await _run(number_setup, coordinator)
    assert _count(added, FluidraChlorinatorLevelNumber) == 1
    assert _count(added, FluidraChlorinatorPhSetpoint) == 0
    assert _count(added, FluidraChlorinatorOrpSetpoint) == 0


async def test_number_setup_chlorinator_without_number_entity():
    """A chlorinator that doesn't declare a 'number' entity gets no level number."""
    chlor = _pin(
        _device("CHL-N3"),
        device_type="chlorinator",
        entities=[],
        features={"ph_setpoint": 8},
    )
    coordinator = _coordinator([chlor])
    added, _ = await _run(number_setup, coordinator)
    assert added == []


async def test_number_setup_light_effect_speed():
    """A light device gets the effect-speed number entity."""
    light = _pin(_device("LT-N1"), device_type="light", entities=["number"])
    coordinator = _coordinator([light])
    added, _ = await _run(number_setup, coordinator)
    assert _count(added, FluidraLightEffectSpeed) == 1


async def test_number_setup_pump_adds_nothing():
    """Pump number branch is currently a no-op (pass)."""
    pump = _pin(_device("PUMP-N1"), device_type="pump", entities=["number"])
    coordinator = _coordinator([pump])
    added, _ = await _run(number_setup, coordinator)
    assert added == []


async def test_number_setup_skips_device_without_id():
    """A device without device_id is skipped."""
    chlor = _pin(_device("CHL-N9"), device_type="chlorinator", entities=["number"])
    chlor["device_id"] = ""
    coordinator = _coordinator([chlor])
    added, _ = await _run(number_setup, coordinator)
    assert added == []


# ---------------------------------------------------------------------------
# TIME platform
# ---------------------------------------------------------------------------
async def test_time_setup_pump_eight_slots():
    """A pump with a 'time' entity gets 8 start + 8 end time entities."""
    pump = _pin(
        _device("PUMP-T1"),
        device_type="pump",
        entities=["time"],
        features={},
    )
    coordinator = _coordinator([pump])
    added, async_add = await _run(time_setup, coordinator)

    async_add.assert_called_once()
    assert _count(added, FluidraScheduleStartTimeEntity) == 8
    assert _count(added, FluidraScheduleEndTimeEntity) == 8


async def test_time_setup_chlorinator_with_schedules():
    """A chlorinator with schedules feature gets start/end times per schedule slot."""
    chlor = _pin(
        _device("CHL-T1"),
        device_type="chlorinator",
        entities=["time"],
        features={"schedules": True, "schedule_count": 2},
    )
    coordinator = _coordinator([chlor])
    added, _ = await _run(time_setup, coordinator)
    assert _count(added, FluidraScheduleStartTimeEntity) == 2
    assert _count(added, FluidraScheduleEndTimeEntity) == 2


async def test_time_setup_light_with_schedule_data():
    """A light with schedule_data in coordinator.data gets start/end time entities."""
    light = _pin(_device("LT-T1"), device_type="light", entities=["time"])
    # coordinator.data must carry the light's schedule_data.
    light_in_data = _pin(
        _device("LT-T1", schedule_data=[{"id": 1}, {"id": 2}]),
        device_type="light",
        entities=["time"],
    )
    data = {POOL_ID: {"id": POOL_ID, "devices": [light_in_data]}}
    coordinator = _coordinator([light], data=data)
    added, _ = await _run(time_setup, coordinator)
    assert _count(added, FluidraLightScheduleStartTimeEntity) == 2
    assert _count(added, FluidraLightScheduleEndTimeEntity) == 2


async def test_time_setup_light_without_schedule_data():
    """A light without schedule_data in coordinator.data gets no time entities."""
    light = _pin(_device("LT-T2"), device_type="light", entities=["time"])
    data = {POOL_ID: {"id": POOL_ID, "devices": [_device("LT-T2")]}}
    coordinator = _coordinator([light], data=data)
    added, _ = await _run(time_setup, coordinator)
    assert _count(added, FluidraLightScheduleStartTimeEntity) == 0
    assert _count(added, FluidraLightScheduleEndTimeEntity) == 0


async def test_time_setup_skip_schedules_continues():
    """skip_schedules (heat pump style) yields no time entities."""
    hp = _pin(
        _device("HP-T1"),
        device_type="pump",
        entities=["time"],
        features={"skip_schedules": True},
    )
    coordinator = _coordinator([hp])
    added, _ = await _run(time_setup, coordinator)
    assert added == []


async def test_time_setup_pump_without_time_entity():
    """A pump that doesn't declare a 'time' entity gets no time entities."""
    pump = _pin(_device("PUMP-T2"), device_type="pump", entities=[], features={})
    coordinator = _coordinator([pump])
    added, _ = await _run(time_setup, coordinator)
    assert added == []


async def test_time_setup_skips_device_without_id():
    """A device without device_id is skipped."""
    pump = _pin(_device("PUMP-T9"), device_type="pump", entities=["time"])
    pump["device_id"] = ""
    coordinator = _coordinator([pump])
    added, _ = await _run(time_setup, coordinator)
    assert added == []


# ---------------------------------------------------------------------------
# LIGHT platform
# ---------------------------------------------------------------------------
async def test_light_setup_by_device_type():
    """A device of type 'light' yields a FluidraLight."""
    light = _device("LT-L1", type="light")
    data = {POOL_ID: {"id": POOL_ID, "devices": [light]}}
    coordinator = _coordinator([light], data=data)
    added, async_add = await _run(light_setup, coordinator)

    async_add.assert_called_once()
    assert _count(added, FluidraLight) == 1


async def test_light_setup_by_family_substring():
    """A device whose family contains 'light' yields a FluidraLight."""
    light = _device("LT-L2", type="", family="LumiPlus Light")
    data = {POOL_ID: {"id": POOL_ID, "devices": [light]}}
    coordinator = _coordinator([light], data=data)
    added, _ = await _run(light_setup, coordinator)
    assert _count(added, FluidraLight) == 1


async def test_light_setup_non_light_ignored():
    """A non-light device produces no light entities."""
    pump = _device("PUMP-L1", type="pump")
    data = {POOL_ID: {"id": POOL_ID, "devices": [pump]}}
    coordinator = _coordinator([pump], data=data)
    added, _ = await _run(light_setup, coordinator)
    assert _count(added, FluidraLight) == 0


async def test_light_setup_light_without_device_id_skipped():
    """A light device without a device_id is skipped."""
    light = _device("LT-L3", type="light")
    light["device_id"] = ""
    data = {POOL_ID: {"id": POOL_ID, "devices": [light]}}
    coordinator = _coordinator([light], data=data)
    added, _ = await _run(light_setup, coordinator)
    assert _count(added, FluidraLight) == 0


async def test_light_setup_triggers_first_refresh_when_no_data():
    """When coordinator.data is empty, the first-refresh hook is awaited."""
    coordinator = _coordinator([], data={})
    coordinator.data = {}
    added, _ = await _run(light_setup, coordinator)
    coordinator.async_config_entry_first_refresh.assert_awaited_once()
    assert added == []


async def test_light_setup_no_data_after_refresh_adds_nothing():
    """If data is still empty after the refresh hook, no entities are added."""
    coordinator = _coordinator([], data=None)
    coordinator.data = None
    coordinator.async_config_entry_first_refresh = AsyncMock()
    added, async_add = await _run(light_setup, coordinator)
    coordinator.async_config_entry_first_refresh.assert_awaited_once()
    # With no devices the platform now skips the empty async_add_entities call.
    async_add.assert_not_called()
    assert added == []
