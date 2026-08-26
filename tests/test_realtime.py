"""Tests for the realtime WebSocket channel (plan 013, Pass 5).

Every behaviour asserted here comes from a measurement recorded in
``API-DISCOVERY.md`` §10: the frame shape, the asynchronous replies, the abrupt
1006 close on idle timeout, and the fact that the channel must never be able to
break the polling path.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import MagicMock

import pytest

from custom_components.fluidra_pool.fluidra_api._websocket import (
    ComponentChange,
    FluidraWebsocketClient,
    parse_frame,
)

DEVICE_ID = "LE24500883"

# Verbatim from the measured session (API-DISCOVERY.md §10.3).
REAL_FRAME = (
    '{"statusCode":200,"action":"componentChange",'
    '"body":"{\\"deviceId\\":\\"LE24500883\\",\\"deviceType\\":\\"connected\\",'
    '\\"componentId\\":11,\\"reportedValue\\":1,\\"ts\\":1787767038}"}'
)
SUBSCRIBE_ACK = (
    '{"statusCode":200,"action":"subsDevice",'
    '"body":"{\\"deviceId\\":\\"LE24500883\\",\\"deviceType\\":\\"connected\\",\\"message\\":\\"OK\\"}"}'
)
FORBIDDEN = '{"message": "Forbidden", "connectionId":"abc==", "requestId":"def="}'


# --- Frame parsing -----------------------------------------------------------


def test_parses_the_measured_frame() -> None:
    """The exact payload the cloud sent during the end-to-end measurement."""
    change = parse_frame(REAL_FRAME)
    assert change == ComponentChange(device_id=DEVICE_ID, component_id=11, reported_value=1, timestamp=1787767038)


def test_parses_bytes() -> None:
    """aiohttp can hand the payload over as bytes."""
    assert parse_frame(REAL_FRAME.encode()) is not None


def test_parses_an_already_decoded_body() -> None:
    """The body is a JSON string in practice, but a dict must work too."""
    change = parse_frame(
        {"action": "componentChange", "body": {"deviceId": DEVICE_ID, "componentId": 9, "reportedValue": 0}}
    )
    assert change is not None
    assert change.component_id == 9
    assert change.timestamp is None


@pytest.mark.parametrize(
    "payload",
    [
        SUBSCRIBE_ACK,  # subscription acknowledgement, not a state change
        FORBIDDEN,  # reply to an unknown action
        "not json",
        b"\xff\xfe",  # undecodable bytes
        None,
        42,
        '{"action":"componentChange"}',  # no body
        '{"action":"componentChange","body":"not json"}',
        '{"action":"componentChange","body":{"componentId":11,"reportedValue":1}}',  # no device
        '{"action":"componentChange","body":{"deviceId":"","componentId":11,"reportedValue":1}}',
        '{"action":"componentChange","body":{"deviceId":"X","reportedValue":1}}',  # no component
        '{"action":"componentChange","body":{"deviceId":"X","componentId":"n/a","reportedValue":1}}',
        '{"action":"componentChange","body":{"deviceId":"X","componentId":11}}',  # no value
    ],
)
def test_non_changes_are_ignored(payload: Any) -> None:
    """Anything that is not a usable state change parses to None, never raises."""
    assert parse_frame(payload) is None


def test_a_zero_value_is_a_real_change() -> None:
    """0 is a legitimate reported value and must not be mistaken for absent."""
    change = parse_frame(
        {"action": "componentChange", "body": {"deviceId": "X", "componentId": 11, "reportedValue": 0}}
    )
    assert change is not None
    assert change.reported_value == 0


def test_unparsable_timestamp_does_not_lose_the_change() -> None:
    """A bad ts costs the timestamp, not the change."""
    change = parse_frame(
        {"action": "componentChange", "body": {"deviceId": "X", "componentId": 11, "reportedValue": 3, "ts": "n/a"}}
    )
    assert change is not None
    assert change.timestamp is None


# --- Client behaviour --------------------------------------------------------


class _FakeWS:
    """Minimal stand-in for an aiohttp WebSocket response."""

    def __init__(self, frames: list[Any]) -> None:
        self._frames = frames
        self.sent: list[dict[str, Any]] = []
        self.pings = 0
        self.closed = False

    async def send_json(self, payload: dict[str, Any]) -> None:
        self.sent.append(payload)

    async def ping(self, data: bytes = b"") -> None:
        self.pings += 1

    async def close(self) -> None:
        self.closed = True

    async def __aenter__(self) -> _FakeWS:
        return self

    async def __aexit__(self, *args: Any) -> None:
        self.closed = True

    def __aiter__(self) -> Any:
        async def gen() -> Any:
            for frame in self._frames:
                message = MagicMock()
                message.type = _TEXT
                message.data = frame
                yield message

        return gen()


class _TextType:
    pass


_TEXT = _TextType()


def _client(frames: list[Any], on_change: Any, token: str | None = "token") -> tuple[FluidraWebsocketClient, _FakeWS]:
    ws = _FakeWS(frames)
    session = MagicMock()
    session.ws_connect = MagicMock(return_value=ws)
    client = FluidraWebsocketClient(session, lambda: token, on_change)
    return client, ws


@pytest.fixture(autouse=True)
def _text_message_type(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make the fake frames look like aiohttp TEXT messages."""
    import custom_components.fluidra_pool.fluidra_api._websocket as module

    monkeypatch.setattr(module.aiohttp, "WSMsgType", MagicMock(TEXT=_TEXT))


async def test_subscribes_to_every_device_on_connect() -> None:
    """One subsDevice per known device, sent without waiting for a reply."""
    client, ws = _client([], lambda change: None)
    client._device_ids = [DEVICE_ID, "OTHER"]

    await client._connect_once()

    assert ws.sent == [
        {"action": "subsDevice", "deviceType": "connected", "deviceId": DEVICE_ID},
        {"action": "subsDevice", "deviceType": "connected", "deviceId": "OTHER"},
    ]


async def test_changes_reach_the_callback() -> None:
    """The measured frame ends up as a ComponentChange on the callback."""
    seen: list[ComponentChange] = []
    client, _ = _client([SUBSCRIBE_ACK, REAL_FRAME], seen.append)
    client._device_ids = [DEVICE_ID]

    await client._connect_once()

    assert [(c.device_id, c.component_id, c.reported_value) for c in seen] == [(DEVICE_ID, 11, 1)]
    assert client.changes_received == 1


async def test_async_callbacks_are_awaited() -> None:
    """The coordinator's handler is a coroutine — it must actually run."""
    seen: list[ComponentChange] = []

    async def handler(change: ComponentChange) -> None:
        await asyncio.sleep(0)
        seen.append(change)

    client, _ = _client([REAL_FRAME], handler)
    await client._connect_once()

    assert len(seen) == 1


async def test_a_failing_callback_does_not_close_the_channel() -> None:
    """A bad handler must not cost the connection, nor the frames after it."""
    seen: list[ComponentChange] = []

    def handler(change: ComponentChange) -> None:
        if not seen:
            seen.append(change)
            raise RuntimeError("boom")
        seen.append(change)

    client, _ = _client([REAL_FRAME, REAL_FRAME], handler)
    await client._connect_once()

    assert len(seen) == 2


async def test_refuses_to_connect_without_a_token() -> None:
    """The handshake validates the token, so there is no point trying."""
    client, _ = _client([], lambda change: None, token=None)
    with pytest.raises(RuntimeError):
        await client._connect_once()


async def test_update_devices_subscribes_only_the_new_ones() -> None:
    """A device discovered on a later poll is subscribed without reconnecting."""
    client, ws = _client([], lambda change: None)
    client._device_ids = [DEVICE_ID]
    client._ws = ws

    await client.update_devices([DEVICE_ID, "NEW"])

    assert ws.sent == [{"action": "subsDevice", "deviceType": "connected", "deviceId": "NEW"}]
    assert client._device_ids == [DEVICE_ID, "NEW"]


async def test_update_devices_is_a_noop_without_a_connection() -> None:
    """The new ids are remembered for the next connect, nothing is sent."""
    client, _ = _client([], lambda change: None)
    client._device_ids = [DEVICE_ID]

    await client.update_devices([DEVICE_ID, "NEW"])

    assert client._device_ids == [DEVICE_ID, "NEW"]


async def test_stop_is_safe_before_start() -> None:
    """Teardown must never raise, whatever state the channel is in."""
    client, _ = _client([], lambda change: None)
    await client.stop()
    assert client.connected is False


# --- Coordinator wiring ------------------------------------------------------


def _coordinator(options: dict[str, Any] | None = None) -> Any:
    """Build a real coordinator over a mock API, with no HA event loop needed."""
    from unittest.mock import AsyncMock

    from custom_components.fluidra_pool.coordinator import FluidraDataUpdateCoordinator

    hass = MagicMock()
    api = MagicMock()
    api.access_token = "token"
    api.write_verifier = None
    entry = MagicMock()
    entry.options = options if options is not None else {}

    coordinator = FluidraDataUpdateCoordinator.__new__(FluidraDataUpdateCoordinator)
    coordinator.hass = hass
    coordinator.api = api
    coordinator.config_entry = entry
    coordinator._realtime = None
    coordinator.realtime_changes = 0
    coordinator.async_set_updated_data = MagicMock()
    coordinator.async_request_refresh = AsyncMock()
    coordinator.data = {
        "pool-1": {
            "id": "pool-1",
            "devices": [
                {"device_id": DEVICE_ID, "name": "Pump", "type": "pump", "components": {"11": {"reportedValue": 0}}}
            ],
        }
    }
    return coordinator


def test_realtime_is_off_by_default() -> None:
    """Opt-in: an entry that never enabled it does not open the channel."""
    assert _coordinator().realtime_enabled is False


def test_realtime_reads_the_option() -> None:
    """The toggle from the options flow is what decides."""
    from custom_components.fluidra_pool.const import CONF_ENABLE_REALTIME

    assert _coordinator({CONF_ENABLE_REALTIME: True}).realtime_enabled is True


def test_known_device_ids_walks_every_pool() -> None:
    """Subscriptions cover the devices the coordinator actually knows."""
    assert _coordinator()._known_device_ids() == [DEVICE_ID]


async def test_pushed_change_updates_the_component_and_notifies() -> None:
    """A push lands on the same decoder as a poll, then wakes the entities."""
    coordinator = _coordinator()
    change = ComponentChange(device_id=DEVICE_ID, component_id=11, reported_value=2, timestamp=1787767038)

    await coordinator._handle_realtime_change(change)

    device = coordinator.data["pool-1"]["devices"][0]
    assert device["components"]["11"]["reportedValue"] == 2
    assert device["components"]["11"]["ts"] == 1787767038
    assert coordinator.realtime_changes == 1
    coordinator.async_set_updated_data.assert_called_once_with(coordinator.data)


async def test_change_for_an_unknown_device_is_ignored() -> None:
    """Discovery stays the poll's job; a stray push must not invent a device."""
    coordinator = _coordinator()

    await coordinator._handle_realtime_change(
        ComponentChange(device_id="SOMEONE-ELSE", component_id=11, reported_value=2)
    )

    assert coordinator.realtime_changes == 0
    coordinator.async_set_updated_data.assert_not_called()


async def test_change_before_the_first_poll_is_ignored() -> None:
    """No data yet means nothing to update, and no crash."""
    coordinator = _coordinator()
    coordinator.data = None

    await coordinator._handle_realtime_change(ComponentChange(device_id=DEVICE_ID, component_id=11, reported_value=2))

    assert coordinator.realtime_changes == 0


async def test_start_does_nothing_when_the_option_is_off() -> None:
    """No option, no connection, no request."""
    coordinator = _coordinator()

    await coordinator.async_start_realtime()

    assert coordinator._realtime is None


async def test_stop_is_safe_when_nothing_was_started() -> None:
    """Unload must never raise because the channel was never opened."""
    coordinator = _coordinator()
    await coordinator.async_stop_realtime()
    assert coordinator._realtime is None
