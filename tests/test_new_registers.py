"""Tests for the registers added by Pass 1 of the API-discovery plan.

Covers the UV lamp block (presence mask + running hours), the split
hours/minutes boost countdown of the tecnoLC2/CC chlorinators, and the
controller-reported filtration state — all read-only, all profile-declared.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from custom_components.fluidra_pool.binary_sensor import (
    FluidraFiltrationStateBinarySensor,
    FluidraUvPresentBinarySensor,
)
from custom_components.fluidra_pool.binary_sensor import (
    async_setup_entry as binary_sensor_setup_entry,
)
from custom_components.fluidra_pool.device_registry.configs.chlorinators import CHLORINATOR_CONFIGS
from custom_components.fluidra_pool.device_registry.configs.pumps import PUMP_CONFIGS
from custom_components.fluidra_pool.sensor import (
    FluidraBoostRemainingHoursSensor,
    FluidraUvRunningHoursSensor,
)
from custom_components.fluidra_pool.sensor import async_setup_entry as sensor_setup_entry

POOL_ID = "pool-1"
DEVICE_ID = "TEST-DEV-001"

UV_PRESENT = 252
UV_HOURS = 253
BOOST_HOURS = 118
BOOST_MINUTES = 111
FILTRATION_STATE = 135
FILTRATION_FALLBACK = 244


def _device(components: dict | None = None, features: dict | None = None, device_id: str = DEVICE_ID) -> dict:
    """Build a chlorinator whose identify cache is pinned to a known feature set."""
    return {
        "device_id": device_id,
        "name": "Chlorinator",
        "family": "Chlorinators",
        "type": "chlorinator",
        "model": "Chlorinator",
        "online": True,
        "components": components if components is not None else {},
        "_identify_cache": {
            "key": (device_id, "Chlorinators", "Chlorinator", "chlorinator", ""),
            "config": SimpleNamespace(
                device_type="chlorinator",
                features=features or {},
                components_range=25,
                required_components=[0, 1, 2, 3],
                entities=["sensor_info"],
            ),
        },
    }


def _coord(devices: list[dict]) -> Any:
    coordinator = MagicMock()
    coordinator.data = {POOL_ID: {"id": POOL_ID, "name": "Pool", "devices": devices}}
    coordinator.last_update_success = True
    return coordinator


# --- UV presence mask (c252) -------------------------------------------------


def _uv_present(device: dict) -> FluidraUvPresentBinarySensor:
    return FluidraUvPresentBinarySensor(_coord([device]), SimpleNamespace(), POOL_ID, DEVICE_ID, UV_PRESENT)


def test_uv_present_true_when_block_reported() -> None:
    """A non-zero mask means the app shows the UV block, so the lamp is there."""
    assert _uv_present(_device({"252": {"reportedValue": 1}})).is_on is True


def test_uv_present_false_when_masked() -> None:
    """A zero mask is the app's own "no UV lamp" signal."""
    assert _uv_present(_device({"252": {"reportedValue": 0}})).is_on is False


def test_uv_present_none_when_register_absent() -> None:
    """A firmware that never reports the register stays unknown, not off."""
    assert _uv_present(_device({})).is_on is None


def test_uv_present_none_on_unparsable_value() -> None:
    """Unparsable readings degrade to unknown rather than raising."""
    assert _uv_present(_device({"252": {"reportedValue": "n/a"}})).is_on is None


def test_uv_present_unavailable_without_components() -> None:
    """No component data at all means unavailable, like the other chlorinator sensors."""
    assert _uv_present(_device({})).available is False


# --- UV running hours (c253) -------------------------------------------------


def _uv_hours(device: dict) -> FluidraUvRunningHoursSensor:
    return FluidraUvRunningHoursSensor(_coord([device]), SimpleNamespace(), POOL_ID, DEVICE_ID, UV_HOURS)


def test_uv_running_hours_value() -> None:
    """The counter is a plain integer hour count (factor 1)."""
    assert _uv_hours(_device({"253": {"reportedValue": 1234}})).native_value == 1234


def test_uv_running_hours_accepts_float_reading() -> None:
    """A float-shaped reading is truncated to whole hours, not rejected."""
    assert _uv_hours(_device({"253": {"reportedValue": 12.0}})).native_value == 12


def test_uv_running_hours_none_on_unparsable_value() -> None:
    """Unparsable readings degrade to None."""
    assert _uv_hours(_device({"253": {"reportedValue": "n/a"}})).native_value is None


def test_uv_running_hours_unavailable_when_register_missing() -> None:
    """A unit without a UV lamp must not show a permanently zero counter."""
    sensor = _uv_hours(_device({"172": {"reportedValue": 250}}))
    assert sensor.available is False
    assert sensor.native_value is None


def test_uv_running_hours_available_when_register_reported() -> None:
    """The register answering is what makes the counter meaningful."""
    assert _uv_hours(_device({"253": {"reportedValue": 0}})).available is True


# --- Boost countdown split over hours + minutes (c118 / c111) ----------------


def _boost(components: dict, feature: dict | None = None) -> FluidraBoostRemainingHoursSensor:
    features = {
        "boost_remaining_hours": feature if feature is not None else {"hours": BOOST_HOURS, "minutes": BOOST_MINUTES}
    }
    return FluidraBoostRemainingHoursSensor(
        _coord([_device(components, features)]), SimpleNamespace(), POOL_ID, DEVICE_ID
    )


def test_boost_remaining_combines_both_halves() -> None:
    """23 h 30 min left reads as 23.5 h, the way the app's own label builds it."""
    assert _boost({"118": {"reportedValue": 23}, "111": {"reportedValue": 30}}).native_value == 23.5


def test_boost_remaining_hours_only() -> None:
    """A firmware reporting only whole hours still yields a value."""
    assert _boost({"118": {"reportedValue": 12}}).native_value == 12.0


def test_boost_remaining_minutes_only() -> None:
    """A firmware reporting only the minutes of the last hour still yields a value."""
    assert _boost({"111": {"reportedValue": 45}}).native_value == 0.75


def test_boost_remaining_zero_is_a_real_reading() -> None:
    """Boost off reports 0, which must stay 0 rather than turning unknown."""
    assert _boost({"118": {"reportedValue": 0}, "111": {"reportedValue": 0}}).native_value == 0.0


def test_boost_remaining_none_when_neither_register_answers() -> None:
    """Neither half reported means unknown."""
    assert _boost({"172": {"reportedValue": 250}}).native_value is None


def test_boost_remaining_none_without_feature_mapping() -> None:
    """A device whose profile declares no register pair reports nothing."""
    sensor = FluidraBoostRemainingHoursSensor(
        _coord([_device({"118": {"reportedValue": 5}}, {})]), SimpleNamespace(), POOL_ID, DEVICE_ID
    )
    assert sensor.native_value is None


def test_boost_remaining_ignores_unparsable_half() -> None:
    """One bad half does not sink the other."""
    assert _boost({"118": {"reportedValue": 3}, "111": {"reportedValue": "n/a"}}).native_value == 3.0


# --- Filtration state (c135, c244 fallback) ---------------------------------


def _filtration(components: dict, fallback: int | None = FILTRATION_FALLBACK) -> FluidraFiltrationStateBinarySensor:
    return FluidraFiltrationStateBinarySensor(
        _coord([_device(components)]),
        SimpleNamespace(),
        POOL_ID,
        DEVICE_ID,
        FILTRATION_STATE,
        fallback,
    )


def test_filtration_running_from_primary_register() -> None:
    """The primary register wins whenever it answers."""
    assert _filtration({"135": {"reportedValue": 1}}).is_on is True


def test_filtration_stopped_from_primary_register() -> None:
    """Zero on the primary register means the block is stopped."""
    assert _filtration({"135": {"reportedValue": 0}, "244": {"reportedValue": 2}}).is_on is False


def test_filtration_second_speed_still_counts_as_running() -> None:
    """The block state is 0/1/2 — anything non-zero is running."""
    assert _filtration({"135": {"reportedValue": 2}}).is_on is True


def test_filtration_falls_back_when_primary_absent() -> None:
    """Firmware versions that only carry the second register still report."""
    assert _filtration({"244": {"reportedValue": 1}}).is_on is True


def test_filtration_none_when_no_register_answers() -> None:
    """Neither register reported means unknown, not stopped."""
    assert _filtration({"172": {"reportedValue": 250}}).is_on is None


def test_filtration_none_without_fallback_declared() -> None:
    """A profile may declare no fallback at all."""
    assert _filtration({"244": {"reportedValue": 1}}, fallback=None).is_on is None


def test_filtration_none_on_unparsable_value() -> None:
    """Unparsable readings degrade to unknown."""
    assert _filtration({"135": {"reportedValue": "n/a"}}).is_on is None


# --- Platform wiring ---------------------------------------------------------


async def _run_setup(setup_entry: Any, devices: list[dict]) -> list[Any]:
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
    await setup_entry(MagicMock(), entry, async_add)
    return added


async def test_binary_sensors_created_only_when_features_declared() -> None:
    """UV presence and filtration state are opt-in per profile."""
    with_features = _device(
        {"252": {"reportedValue": 1}, "135": {"reportedValue": 1}},
        {
            "uv_lamp": {"present": UV_PRESENT, "running_hours": UV_HOURS},
            "filtration_state": {"state": FILTRATION_STATE, "fallback": FILTRATION_FALLBACK},
        },
        device_id="dev1",
    )
    without_features = _device({"252": {"reportedValue": 1}}, {}, device_id="dev2")
    added = await _run_setup(binary_sensor_setup_entry, [with_features, without_features])

    uids = {e.unique_id for e in added}
    assert "fluidra_pool_pool-1_dev1_uv_present" in uids
    assert "fluidra_pool_pool-1_dev1_filtration_state" in uids
    assert "fluidra_pool_pool-1_dev2_uv_present" not in uids
    assert "fluidra_pool_pool-1_dev2_filtration_state" not in uids


async def test_sensors_created_only_when_features_declared() -> None:
    """The UV counter and the split boost countdown are opt-in per profile."""
    with_features = _device(
        {"253": {"reportedValue": 10}, "118": {"reportedValue": 1}},
        {
            "uv_lamp": {"present": UV_PRESENT, "running_hours": UV_HOURS},
            "boost_remaining_hours": {"hours": BOOST_HOURS, "minutes": BOOST_MINUTES},
        },
        device_id="dev1",
    )
    without_features = _device({"253": {"reportedValue": 10}}, {}, device_id="dev2")
    added = await _run_setup(sensor_setup_entry, [with_features, without_features])

    uids = {e.unique_id for e in added}
    assert "fluidra_dev1_uv_running_hours" in uids
    assert "fluidra_dev1_boost_remaining_hours" in uids
    assert "fluidra_dev2_uv_running_hours" not in uids
    assert "fluidra_dev2_boost_remaining_hours" not in uids


# --- Profile declarations (plan 013, Pass 1) ---------------------------------


def test_tecnolc2_profiles_declare_the_ui_config_registers() -> None:
    """Every tecnoLC2 chlorinator gains the UV block and the filtration state."""
    for name, config in CHLORINATOR_CONFIGS.items():
        if config.features.get("sensors", {}).get("temperature") != 172:
            continue
        features = config.features
        assert features["uv_lamp"] == {"present": UV_PRESENT, "running_hours": UV_HOURS}, name
        assert features["filtration_state"] == {
            "state": FILTRATION_STATE,
            "fallback": FILTRATION_FALLBACK,
        }, name
        scanned = set(features["specific_components"])
        assert {UV_PRESENT, UV_HOURS, FILTRATION_STATE, FILTRATION_FALLBACK} <= scanned, name


def test_boost_countdown_only_on_profiles_that_have_a_boost() -> None:
    """The hours/minutes pair is meaningless on a unit with no boost register."""
    for name, config in CHLORINATOR_CONFIGS.items():
        features = config.features
        if features.get("sensors", {}).get("temperature") != 172:
            continue
        if features.get("boost_mode") is None:
            assert "boost_remaining_hours" not in features, name
            continue
        assert features["boost_remaining_hours"] == {"hours": BOOST_HOURS, "minutes": BOOST_MINUTES}, name
        assert {BOOST_HOURS, BOOST_MINUTES} <= set(features["specific_components"]), name


def test_non_tecnolc2_chlorinators_are_left_alone() -> None:
    """The catch-all, the DM lineup and the eXO read these ids differently."""
    for name in ("chlorinator", "dm24049704_chlorinator", "ns25_exo_chlorinator"):
        features = CHLORINATOR_CONFIGS[name].features
        assert "uv_lamp" not in features, name
        assert "filtration_state" not in features, name
        assert "boost_remaining_hours" not in features, name


def test_victoria_pump_keeps_its_own_meaning_for_c135() -> None:
    """c135 is the active quick-function slot there — it must never be read as filtration."""
    features = PUMP_CONFIGS["victoria_smart_connect_pump"].features
    assert "filtration_state" not in features
    assert FILTRATION_STATE in features["specific_components"]
