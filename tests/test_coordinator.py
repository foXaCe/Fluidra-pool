"""Tests for Fluidra Pool data update coordinator."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import UpdateFailed
import pytest

from custom_components.fluidra_pool.api_resilience import FluidraConnectionError
from custom_components.fluidra_pool.const import OFFLINE_GRACE_POLLS, STALE_DEVICE_THRESHOLD
from custom_components.fluidra_pool.coordinator import FluidraDataUpdateCoordinator


@pytest.fixture
def coordinator(hass: HomeAssistant, mock_api: AsyncMock) -> FluidraDataUpdateCoordinator:
    """Create a coordinator with mock API."""
    return FluidraDataUpdateCoordinator(hass, mock_api)


class TestCoordinatorInit:
    """Test coordinator initialization."""

    async def test_creates_with_defaults(self, hass: HomeAssistant, mock_api: AsyncMock):
        coord = FluidraDataUpdateCoordinator(hass, mock_api)
        assert coord.api is mock_api
        assert coord.name == "fluidra_pool"
        assert coord._first_update is True

    async def test_creates_with_config_entry(self, hass: HomeAssistant, mock_api: AsyncMock):
        coord = FluidraDataUpdateCoordinator(hass, mock_api, config_entry=None)
        assert coord.config_entry is None


class TestGetPoolsFromData:
    """Test get_pools_from_data method."""

    async def test_returns_empty_when_no_data(self, coordinator: FluidraDataUpdateCoordinator):
        coordinator.data = None
        assert coordinator.get_pools_from_data() == []

    async def test_returns_pools_from_data(self, coordinator: FluidraDataUpdateCoordinator, mock_pool_data: dict):
        coordinator.data = mock_pool_data
        pools = coordinator.get_pools_from_data()
        assert len(pools) == 1
        assert pools[0]["id"] == "pool_001"


class TestAsyncUpdateData:
    """Test _async_update_data method."""

    async def test_first_update_returns_minimal_data(self, coordinator: FluidraDataUpdateCoordinator):
        """First update should return pool structure only for fast startup."""
        result = await coordinator._async_update_data()
        assert "pool_001" in result
        assert coordinator._first_update is False

    async def test_pools_without_id_are_dropped(self, coordinator: FluidraDataUpdateCoordinator, mock_api: AsyncMock):
        """A malformed pool without an id must be skipped, not crash the update."""
        mock_api.ensure_valid_token.return_value = True
        mock_api.get_pools.return_value = [
            {"id": "pool_001", "name": "Good", "devices": []},
            {"name": "No id", "devices": []},
        ]
        result = await coordinator._async_update_data()
        assert "pool_001" in result
        assert len(result) == 1

    async def test_second_update_fetches_components(
        self, coordinator: FluidraDataUpdateCoordinator, mock_api: AsyncMock
    ):
        """Second update should fetch component details."""
        # First update
        await coordinator._async_update_data()
        # Second update
        await coordinator._async_update_data()
        assert mock_api.get_pool_details.called
        assert mock_api.poll_pool_device_statuses.called

    async def test_raises_auth_failed_on_invalid_token(
        self, coordinator: FluidraDataUpdateCoordinator, mock_api: AsyncMock
    ):
        """Should raise ConfigEntryAuthFailed when token is invalid."""
        mock_api.ensure_valid_token.return_value = False

        with pytest.raises(ConfigEntryAuthFailed):
            await coordinator._async_update_data()

    async def test_raises_update_failed_on_error(self, coordinator: FluidraDataUpdateCoordinator, mock_api: AsyncMock):
        """Should raise UpdateFailed on API errors."""
        mock_api.ensure_valid_token.return_value = True
        mock_api.get_pools.side_effect = FluidraConnectionError("Network error")

        with pytest.raises(UpdateFailed, match="Error communicating with API"):
            await coordinator._async_update_data()

    async def test_empty_pools_does_not_purge_devices(
        self, coordinator: FluidraDataUpdateCoordinator, mock_api: AsyncMock
    ):
        """A transient empty get_pools must not run registry cleanup (devices vanish on restart)."""
        mock_api.ensure_valid_token.return_value = True
        # First (minimal) update clears the _first_update flag.
        await coordinator._async_update_data()
        # A later poll returns an empty pool list (transient cloud hiccup).
        mock_api.get_pools.return_value = []
        with patch.object(coordinator, "_cleanup_removed_devices", new=AsyncMock()) as cleanup:
            result = await coordinator._async_update_data()

        cleanup.assert_not_called()
        assert result == {}


class TestProcessComponentState:
    """Test _process_component_state method."""

    async def test_component_0_sets_device_id(self, coordinator: FluidraDataUpdateCoordinator):
        device = {"device_id": "test", "type": "pump", "components": {}}
        coordinator._process_component_state(device, "pool_001", 0, {"reportedValue": "DEVICE_ID_123"})
        assert device["device_id_component"] == "DEVICE_ID_123"

    async def test_component_9_sets_pump_state(self, coordinator: FluidraDataUpdateCoordinator):
        device = {"device_id": "test", "type": "pump", "components": {}}
        coordinator._process_component_state(device, "pool_001", 9, {"reportedValue": 1, "desiredValue": 1})
        assert device["is_running"] is True
        assert device["pump_reported"] == 1

    async def test_component_9_pump_off(self, coordinator: FluidraDataUpdateCoordinator):
        device = {"device_id": "test", "type": "pump", "components": {}}
        coordinator._process_component_state(device, "pool_001", 9, {"reportedValue": 0, "desiredValue": 0})
        assert device["is_running"] is False

    async def test_component_10_auto_mode(self, coordinator: FluidraDataUpdateCoordinator):
        device = {"device_id": "test", "type": "pump", "components": {}}
        coordinator._process_component_state(device, "pool_001", 10, {"reportedValue": 1, "desiredValue": 1})
        assert device["auto_mode_enabled"] is True

    async def test_component_13_heat_pump_state(self, coordinator: FluidraDataUpdateCoordinator):
        device = {"device_id": "test", "type": "heat_pump", "components": {}}
        coordinator._process_component_state(device, "pool_001", 13, {"reportedValue": 1})
        assert device["is_heating"] is True

    async def test_component_60_z550_running_hours(self, coordinator: FluidraDataUpdateCoordinator):
        """Z550iQ+ total running hours come from component 60 (Issue #88)."""
        device = {
            "device_id": "LD12345",
            "name": "Z550iQ",
            "family": "heat pump",
            "type": "heat_pump",
            "components": {},
        }
        coordinator._process_component_state(device, "pool_001", 60, {"reportedValue": 4321})
        assert device["running_hours"] == 4321

    async def test_component_61_z550_no_flow(self, coordinator: FluidraDataUpdateCoordinator):
        """Z550iQ+ component 61 = 11 marks the no-flow state (Issue #88)."""
        device = {
            "device_id": "LD12345",
            "name": "Z550iQ",
            "family": "heat pump",
            "type": "heat_pump",
            "components": {},
        }
        coordinator._process_component_state(device, "pool_001", 61, {"reportedValue": 11})
        assert device["z550_state_reported"] == 11
        assert device["hvac_action"] == "no_flow"

    async def test_component_15_temperature(self, coordinator: FluidraDataUpdateCoordinator):
        device = {"device_id": "test", "type": "heat_pump", "components": {}}
        coordinator._process_component_state(device, "pool_001", 15, {"reportedValue": 290})
        assert device["target_temperature"] == 29.0

    async def test_component_15_invalid_temp_ignored(self, coordinator: FluidraDataUpdateCoordinator):
        device = {"device_id": "test", "type": "heat_pump", "components": {}}
        coordinator._process_component_state(device, "pool_001", 15, {"reportedValue": 5})
        assert "target_temperature" not in device

    async def test_component_15_preserves_reported_zero(self, coordinator: FluidraDataUpdateCoordinator):
        """A legitimate reported 0 must be kept, not replaced by desiredValue (coordinator-2)."""
        device = {"device_id": "test", "type": "heat_pump", "components": {}}
        coordinator._process_component_state(device, "pool_001", 15, {"reportedValue": 0, "desiredValue": 290})
        assert device["component_15_speed"] == 0
        # 0 is out of the 10-50 valid range, so no target_temperature is derived.
        assert "target_temperature" not in device

    async def test_component_15_falls_back_to_desired_when_reported_missing(
        self, coordinator: FluidraDataUpdateCoordinator
    ):
        """When reportedValue is absent, desiredValue is still used."""
        device = {"device_id": "test", "type": "heat_pump", "components": {}}
        coordinator._process_component_state(device, "pool_001", 15, {"desiredValue": 290})
        assert device["component_15_speed"] == 290
        assert device["target_temperature"] == 29.0

    async def test_component_20_chlorinator_mode(self, coordinator: FluidraDataUpdateCoordinator):
        device = {"device_id": "test", "type": "chlorinator", "components": {}}
        coordinator._process_component_state(device, "pool_001", 20, {"reportedValue": 2})
        assert device["mode_reported"] == 2

    async def test_component_20_pump_schedule(self, coordinator: FluidraDataUpdateCoordinator):
        schedule = [{"id": 1, "startTime": "0 8 * * 1,2,3", "enabled": True}]
        device = {"device_id": "test", "type": "pump", "components": {}}
        coordinator._process_component_state(device, "pool_001", 20, {"reportedValue": schedule})
        assert device["schedule_data"] == schedule


class TestCleanupRemovedDevices:
    """Test _cleanup_removed_devices confirmation-based purge (stale-devices)."""

    @staticmethod
    def _coord(hass: HomeAssistant, mock_api: AsyncMock) -> FluidraDataUpdateCoordinator:
        entry = MagicMock()
        entry.entry_id = "entry_1"
        entry.options = {}  # Keep the default scan interval (avoid a MagicMock timedelta).
        coord = FluidraDataUpdateCoordinator(hass, mock_api, config_entry=entry)
        # The base DataUpdateCoordinator overwrites config_entry from a ContextVar that
        # isn't set in tests; pin it back so cleanup runs instead of early-returning.
        coord.config_entry = entry
        return coord

    @staticmethod
    def _device_entry() -> MagicMock:
        device_entry = MagicMock()
        device_entry.id = "dev_reg_1"
        device_entry.model = "Pump"  # Not a "Pool" parent, so it is eligible for removal.
        device_entry.identifiers = {("fluidra_pool", "serial_gone")}
        return device_entry

    @staticmethod
    async def _run(
        coord: FluidraDataUpdateCoordinator,
        current_device_ids: set[str],
        *,
        device_entry: MagicMock,
        dev_reg: MagicMock,
        ent_reg: MagicMock,
    ) -> None:
        module = "custom_components.fluidra_pool.coordinator.coordinator"
        with (
            patch(f"{module}.dr.async_get", return_value=dev_reg),
            patch(f"{module}.dr.async_entries_for_config_entry", return_value=[device_entry]),
            patch(f"{module}.er.async_get", return_value=ent_reg),
            patch(f"{module}.er.async_entries_for_device", return_value=[]),
        ):
            await coord._cleanup_removed_devices(current_device_ids=current_device_ids)

    async def test_cleanup_waits_for_threshold_before_purge(self, hass: HomeAssistant, mock_api: AsyncMock):
        """A device must be absent for STALE_DEVICE_THRESHOLD polls before it is purged."""
        coord = self._coord(hass, mock_api)
        device_entry = self._device_entry()
        dev_reg, ent_reg = MagicMock(), MagicMock()

        # Absent for THRESHOLD-1 consecutive polls → not purged yet.
        for _ in range(STALE_DEVICE_THRESHOLD - 1):
            await self._run(coord, {"still_here"}, device_entry=device_entry, dev_reg=dev_reg, ent_reg=ent_reg)
        dev_reg.async_remove_device.assert_not_called()

        # The THRESHOLD-th consecutive absence triggers the purge.
        await self._run(coord, {"still_here"}, device_entry=device_entry, dev_reg=dev_reg, ent_reg=ent_reg)
        dev_reg.async_remove_device.assert_called_once_with("dev_reg_1")

    async def test_cleanup_resets_strikes_when_device_returns(self, hass: HomeAssistant, mock_api: AsyncMock):
        """A device that reappears clears its strike count and is not purged."""
        coord = self._coord(hass, mock_api)
        device_entry = self._device_entry()
        dev_reg, ent_reg = MagicMock(), MagicMock()

        for _ in range(STALE_DEVICE_THRESHOLD - 1):
            await self._run(coord, {"still_here"}, device_entry=device_entry, dev_reg=dev_reg, ent_reg=ent_reg)
        # Device is present again → strike count resets.
        await self._run(coord, {"serial_gone"}, device_entry=device_entry, dev_reg=dev_reg, ent_reg=ent_reg)
        # A fresh absence is only the first strike again → still not purged.
        await self._run(coord, {"still_here"}, device_entry=device_entry, dev_reg=dev_reg, ent_reg=ent_reg)

        dev_reg.async_remove_device.assert_not_called()

    async def test_cleanup_swallows_registry_keyerror(self, hass: HomeAssistant, mock_api: AsyncMock):
        """A KeyError from async_remove_device must not propagate / fail the poll (coordinator-4).

        The device registry's async_remove_device raises a bare KeyError when a
        device was removed concurrently; the best-effort cleanup must absorb it.
        """
        coord = self._coord(hass, mock_api)
        device_entry = self._device_entry()
        dev_reg, ent_reg = MagicMock(), MagicMock()
        dev_reg.async_remove_device.side_effect = KeyError("already removed")
        # Pre-age the device to the brink so a single poll triggers the removal.
        coord._missing_device_counts["serial_gone"] = STALE_DEVICE_THRESHOLD - 1

        await self._run(coord, {"still_here"}, device_entry=device_entry, dev_reg=dev_reg, ent_reg=ent_reg)

        dev_reg.async_remove_device.assert_called_once_with("dev_reg_1")


class TestParseDM24049704Schedule:
    """Test DM24049704 schedule format parsing."""

    async def test_parse_valid_schedule(self, coordinator: FluidraDataUpdateCoordinator):
        reported_value = {
            "dayPrograms": {"monday": 1, "tuesday": 1, "wednesday": 1},
            "programs": [{"id": 1, "slots": [{"id": 0, "start": 1280, "end": 1536, "mode": 3}]}],
        }
        result = coordinator._parse_dm24049704_schedule_format(reported_value)
        assert len(result) == 1
        assert result[0]["enabled"] is True
        assert "startTime" in result[0]
        assert "endTime" in result[0]

    async def test_parse_empty_programs(self, coordinator: FluidraDataUpdateCoordinator):
        result = coordinator._parse_dm24049704_schedule_format({"dayPrograms": {}, "programs": []})
        assert result == []

    async def test_parse_non_dict_returns_empty(self, coordinator: FluidraDataUpdateCoordinator):
        assert coordinator._parse_dm24049704_schedule_format("not a dict") == []
        assert coordinator._parse_dm24049704_schedule_format(None) == []

    async def test_parse_skips_empty_slots(self, coordinator: FluidraDataUpdateCoordinator):
        reported_value = {
            "dayPrograms": {"monday": 1},
            "programs": [{"id": 1, "slots": [{"id": 0, "start": 0, "end": 0, "mode": 0}]}],
        }
        result = coordinator._parse_dm24049704_schedule_format(reported_value)
        assert result == []

    async def test_time_decoding(self, coordinator: FluidraDataUpdateCoordinator):
        """Test that time encoding hours*256+minutes is decoded correctly."""
        # 5:00 = 5*256 + 0 = 1280, 6:00 = 6*256 + 0 = 1536
        reported_value = {
            "dayPrograms": {"monday": 1},
            "programs": [{"id": 1, "slots": [{"id": 0, "start": 1280, "end": 1536, "mode": 1}]}],
        }
        result = coordinator._parse_dm24049704_schedule_format(reported_value)
        assert len(result) == 1
        assert "0 5" in result[0]["startTime"]
        assert "0 6" in result[0]["endTime"]


class TestCalculateAutoSpeed:
    """Test _calculate_auto_speed_from_schedules."""

    async def test_no_schedules_returns_zero(self, coordinator: FluidraDataUpdateCoordinator):
        device = {"schedule_data": []}
        assert coordinator._calculate_auto_speed_from_schedules(device) == 0

    async def test_no_schedule_data_returns_zero(self, coordinator: FluidraDataUpdateCoordinator):
        device = {}
        assert coordinator._calculate_auto_speed_from_schedules(device) == 0


class TestSyncDeviceFirmware:
    """Firmware versions reported by devices are mirrored into the registry."""

    _POOLS = [{"id": "p1", "devices": [{"device_id": "D1", "firmware_version_component": "2.0"}]}]

    def _sync(self, hass: HomeAssistant, mock_api: AsyncMock, pools, registry_entry):
        coord = FluidraDataUpdateCoordinator(hass, mock_api)
        dev_reg = MagicMock()
        dev_reg.async_get_device.return_value = registry_entry
        with patch(
            "custom_components.fluidra_pool.coordinator.coordinator.dr.async_get",
            return_value=dev_reg,
        ):
            coord._sync_device_firmware(pools)
        return dev_reg

    def test_updates_registry_when_firmware_differs(self, hass: HomeAssistant, mock_api: AsyncMock):
        entry = MagicMock()
        entry.id = "reg_1"
        entry.sw_version = "1.0"
        dev_reg = self._sync(hass, mock_api, self._POOLS, entry)
        dev_reg.async_update_device.assert_called_once_with("reg_1", sw_version="2.0")

    def test_noop_when_firmware_unchanged(self, hass: HomeAssistant, mock_api: AsyncMock):
        entry = MagicMock()
        entry.sw_version = "2.0"
        dev_reg = self._sync(hass, mock_api, self._POOLS, entry)
        dev_reg.async_update_device.assert_not_called()

    def test_noop_when_device_not_registered(self, hass: HomeAssistant, mock_api: AsyncMock):
        dev_reg = self._sync(hass, mock_api, self._POOLS, None)
        dev_reg.async_update_device.assert_not_called()

    def test_skips_devices_without_firmware_or_id(self, hass: HomeAssistant, mock_api: AsyncMock):
        pools = [{"id": "p1", "devices": [{"device_id": "D1"}, {"firmware_version_component": "9"}]}]
        dev_reg = self._sync(hass, mock_api, pools, MagicMock())
        dev_reg.async_get_device.assert_not_called()


class TestUpdateDataOutagePath:
    """A whole-cycle refresh failure counts towards the connection issue."""

    async def _run_update(self, hass: HomeAssistant, mock_api: AsyncMock, *, refresh_fails: bool):
        coord = FluidraDataUpdateCoordinator(hass, mock_api)
        coord._first_update = False
        mock_api.ensure_valid_token = AsyncMock(return_value=True)
        mock_api.get_pools = AsyncMock(return_value=[{"id": "p1", "devices": []}])
        side_effect = FluidraConnectionError("cloud down") if refresh_fails else None
        with (
            patch.object(coord, "_refresh_pool", AsyncMock(side_effect=side_effect)),
            patch.object(coord, "_note_update_failure") as note_failure,
            patch.object(coord, "_handle_update_success") as note_success,
            patch.object(coord, "_sync_device_firmware") as sync_fw,
        ):
            data = await coord._async_update_data()
        return data, note_failure, note_success, sync_fw

    async def test_all_pools_failing_counts_as_failure(self, hass: HomeAssistant, mock_api: AsyncMock):
        data, note_failure, note_success, sync_fw = await self._run_update(hass, mock_api, refresh_fails=True)
        note_failure.assert_called_once()
        note_success.assert_not_called()
        sync_fw.assert_not_called()
        assert "p1" in data  # graceful degradation: data is still returned

    async def test_successful_cycle_resets_streak_and_syncs_firmware(self, hass: HomeAssistant, mock_api: AsyncMock):
        data, note_failure, note_success, sync_fw = await self._run_update(hass, mock_api, refresh_fails=False)
        note_failure.assert_not_called()
        note_success.assert_called_once()
        sync_fw.assert_called_once()
        assert "p1" in data


class TestPoolAccessLevel:
    """The coordinator classifies pool access and warns once on viewer (read-only)."""

    async def _refresh(self, hass: HomeAssistant, mock_api: AsyncMock, pool_details: dict, user_id):
        coord = FluidraDataUpdateCoordinator(hass, mock_api)
        mock_api.user_id = user_id
        mock_api.get_pool_details = AsyncMock(return_value=pool_details)
        mock_api.poll_water_quality = AsyncMock(return_value=None)
        mock_api.poll_pool_device_statuses = AsyncMock(return_value=None)
        pool = {"id": "pool_1", "name": "casa", "devices": []}
        await coord._refresh_pool(pool, {})
        return coord, pool

    async def test_viewer_access_flagged_and_warns_once(self, hass: HomeAssistant, mock_api: AsyncMock, caplog):
        details = {"owner": "someone-else", "contracts": [{"id": "me", "accessLevel": "viewer"}]}
        coord, pool = await self._refresh(hass, mock_api, details, "me")
        assert pool["access_level"] == "viewer"
        assert "pool_1" in coord._read_only_pools_warned
        assert any("viewer (read-only) access" in r.message for r in caplog.records)

        # A second refresh of the same pool must not warn again.
        caplog.clear()
        pool2 = {"id": "pool_1", "name": "casa", "devices": []}
        await coord._refresh_pool(pool2, {})
        assert not any("viewer (read-only) access" in r.message for r in caplog.records)

    async def test_owner_access_not_flagged(self, hass: HomeAssistant, mock_api: AsyncMock):
        details = {"owner": "me", "contracts": [{"id": "me", "accessLevel": "viewer"}]}
        _, pool = await self._refresh(hass, mock_api, details, "me")
        assert pool["access_level"] == "owner"


class TestOnlineDebounce:
    """A single offline connectivity report must not flip a device offline (Issue #140).

    The Fluidra heartbeat routinely misreports a healthy device as disconnected
    for one poll; the coordinator only marks a device offline after
    OFFLINE_GRACE_POLLS consecutive offline reports, and recovers immediately.
    """

    def test_single_offline_report_keeps_device_online(self, coordinator: FluidraDataUpdateCoordinator):
        device = {"device_id": "D1", "online": True}
        coordinator._apply_online_flag(device, "D1", False)
        assert device["online"] is True
        assert coordinator._offline_poll_counts["D1"] == 1

    def test_consecutive_offline_reports_mark_device_offline(self, coordinator: FluidraDataUpdateCoordinator):
        device = {"device_id": "D1", "online": True}
        for _ in range(OFFLINE_GRACE_POLLS):
            coordinator._apply_online_flag(device, "D1", False)
        assert device["online"] is False

    def test_online_report_recovers_immediately_and_resets_strikes(self, coordinator: FluidraDataUpdateCoordinator):
        device = {"device_id": "D1", "online": False}
        coordinator._offline_poll_counts["D1"] = OFFLINE_GRACE_POLLS
        coordinator._apply_online_flag(device, "D1", True)
        assert device["online"] is True
        assert "D1" not in coordinator._offline_poll_counts
        # A later isolated offline report starts counting from scratch.
        coordinator._apply_online_flag(device, "D1", False)
        assert device["online"] is True

    async def test_refresh_pool_applies_debounced_flag(
        self, coordinator: FluidraDataUpdateCoordinator, mock_api: AsyncMock
    ):
        """The connectivity flag flows through _refresh_pool with the debounce applied."""
        mock_api.get_pool_details = AsyncMock(return_value={})
        mock_api.poll_water_quality = AsyncMock(return_value=None)
        mock_api.poll_pool_device_statuses = AsyncMock(return_value={"D1": {"connectivity": {"connected": False}}})
        device = {"device_id": "D1", "name": "Heat pump", "online": True, "components": {}}
        pool = {"id": "pool_1", "name": "Pool", "devices": [device]}

        await coordinator._refresh_pool(pool, {})
        assert device["online"] is True  # first strike tolerated

        await coordinator._refresh_pool(pool, {})
        assert device["online"] is False  # second consecutive strike is trusted

        mock_api.poll_pool_device_statuses = AsyncMock(return_value={"D1": {"connectivity": {"connected": True}}})
        await coordinator._refresh_pool(pool, {})
        assert device["online"] is True  # immediate recovery
