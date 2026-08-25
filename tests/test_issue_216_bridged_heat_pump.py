"""Issue #216 — a heat pump bridged by an iQBridge RS must not be read as a chlorinator.

The reporter (@vicsol00-collab) runs an AstralPool Elite Connect LS chlorinator and a
Zodiac PX50 heat pump on the same Fluidra account. Both are published by the cloud under
the same pool, but only the chlorinator produced entities.

The diagnostics dump attached to the issue shows why. Both devices are bridged, so both
carry the ``.nn_`` protocol marker in their cloud id — the PX50's status tree reads
``{"bridgedInfo": {"protocol": "nn"}, "thingType": "proelyo"}`` and its id is
``QS23361258.nn_9``, while its family is ``Heat Pumps`` and its type ``heat_pump``. The
chlorinator catch-all profile listed ``*.nn_*`` among its identifier patterns, so it
scored the 50-point identifier signal on the heat pump and beat every heat-pump profile.
The dump proves it: the PX50's ``_identify_cache`` holds
``DeviceConfig(device_type='chlorinator', identifier_patterns=['*.nn_*'], ...)`` and its
scanned components are the chlorinator set (0, 1, 2, 3, 4, 8, 11, 20) — no climate.

``.nn_`` is a *bridge protocol* marker, not a device family. These tests pin that.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.fluidra_pool.climate import (
    FluidraHeatPumpClimate,
)
from custom_components.fluidra_pool.climate import (
    async_setup_entry as climate_setup,
)
from custom_components.fluidra_pool.device_registry import DEVICE_CONFIGS, DeviceIdentifier

POOL_ID = "pool_4519"


def _px50() -> dict:
    """The Zodiac PX50 exactly as the issue-216 diagnostics report it."""
    return {
        "device_id": "QS23361258.nn_9",
        "name": "PX50",
        "type": "heat_pump",
        "family": "Heat Pumps",
        "model": "PX50",
        "manufacturer": "Fluidra",
        "thing_type": "proelyo",
        "online": True,
        "components": {},
        "status": {
            "info": {"name": "PX50", "family": "Heat Pumps", "core": True},
            "bridgedInfo": {"protocol": "nn"},
            "thingType": "proelyo",
        },
    }


def _elite_connect_ls() -> dict:
    """The AstralPool Elite Connect LS chlorinator from the same dump."""
    return {
        "device_id": "DM24006105.nn_1",
        "name": "Chlorinator",
        "type": "chlorinator",
        "family": "Chlorinators",
        "model": "Chlorinator",
        "manufacturer": "Fluidra",
        "thing_type": "domoticS2",
        "online": True,
        "components": {},
    }


def _config_name(config) -> str | None:
    return next((name for name, value in DEVICE_CONFIGS.items() if value is config), None)


class TestBridgedHeatPumpIdentification:
    """The bridge marker must not decide the device family."""

    def test_px50_is_identified_as_a_heat_pump(self):
        config = DeviceIdentifier.identify_device(_px50())
        assert config is not None
        assert config.device_type == "heat_pump"
        assert _config_name(config) == "generic_heat_pump"

    def test_px50_declares_a_climate_entity(self):
        device = _px50()
        assert DeviceIdentifier.should_create_entity(device, "climate") is True

    def test_px50_scans_the_heat_pump_registers_not_the_chlorinator_ones(self):
        """The chlorinator profile scanned c164/c172/c177/c185; a heat pump needs c13/c14/c15."""
        device = _px50()
        specific = DeviceIdentifier.get_feature(device, "specific_components", [])
        assert set(specific) == {13, 14, 15}
        assert DeviceIdentifier.get_feature(device, "hvac_modes") == ["off", "heat"]
        assert DeviceIdentifier.has_feature(device, "temperature_control") is True

    def test_elite_connect_ls_still_matches_the_chlorinator_catch_all(self):
        """The device that already worked must keep the exact profile it had."""
        config = DeviceIdentifier.identify_device(_elite_connect_ls())
        assert _config_name(config) == "chlorinator"
        assert DeviceIdentifier.should_create_entity(_elite_connect_ls(), "switch") is True

    def test_bridge_protocol_is_not_a_chlorinator_identifier(self):
        """``*.nn_*`` means "bridged over protocol nn", so no profile may claim it."""
        for name, config in DEVICE_CONFIGS.items():
            assert "*.nn_*" not in config.identifier_patterns, (
                f"{name} claims every bridged device via the '*.nn_*' protocol marker"
            )

    @pytest.mark.parametrize(
        "name",
        ["z250iq_heat_pump", "z260iq_heat_pump", "z550iq_heat_pump", "z650iq_heat_pump"],
    )
    def test_model_specific_heat_pumps_do_not_claim_the_family(self, name):
        """Same rule the HPGIC profile already documents: a bare "heat pump" family match
        would steal genuinely-unknown heat pumps from the generic fallback."""
        assert DEVICE_CONFIGS[name].family_patterns == []

    def test_serial_specific_chlorinators_do_not_claim_the_family(self):
        """Same rule on the other lineup: only the catch-all may match "Chlorinators"."""
        claimers = [
            name
            for name, config in DEVICE_CONFIGS.items()
            if "chlorinator" in config.family_patterns and config.identifier_patterns
        ]
        assert claimers == []


class TestBridgedHeatPumpClimateEntity:
    """End-to-end: the climate platform must build an entity for the PX50."""

    @staticmethod
    def _entry(devices):
        coordinator = MagicMock()
        pool = {"id": POOL_ID, "name": "Rumoroso", "devices": devices}
        coordinator.data = {POOL_ID: pool}
        coordinator.last_update_success = True
        coordinator.async_request_refresh = AsyncMock()
        coordinator.api = SimpleNamespace(
            cached_pools=[pool],
            get_pools=AsyncMock(return_value=[pool]),
        )
        return SimpleNamespace(
            runtime_data=SimpleNamespace(coordinator=coordinator),
            async_on_unload=lambda _unsub: None,
        )

    async def _added(self, devices) -> list:
        added: list = []
        await climate_setup(
            MagicMock(),
            self._entry(devices),
            MagicMock(side_effect=lambda entities, *a, **k: added.extend(list(entities))),
        )
        return added

    async def test_climate_entity_created_for_the_px50(self):
        added = await self._added([_elite_connect_ls(), _px50()])
        climates = [e for e in added if isinstance(e, FluidraHeatPumpClimate)]
        assert len(climates) == 1
        assert climates[0].unique_id == f"fluidra_pool_{POOL_ID}_QS23361258.nn_9_climate"

    async def test_no_climate_entity_for_the_chlorinator(self):
        added = await self._added([_elite_connect_ls()])
        assert added == []
