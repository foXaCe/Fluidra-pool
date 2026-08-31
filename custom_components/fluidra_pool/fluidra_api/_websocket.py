"""Realtime channel: the cloud pushes component changes instead of being polled.

Measured end to end on 2026-08-26 (see ``API-DISCOVERY.md`` §10), and every
design decision here follows from one of those measurements:

* the handshake authenticates with the Cognito access token as a **query
  parameter** — an ``Authorization`` header gets a 401;
* a silent connection is closed at **600 s exactly** with ``close_code=1006``,
  the AWS API Gateway idle timeout, so a keepalive well under that is not
  optional and an abrupt 1006 is a normal event, not an error;
* the token is checked **only at handshake time** — a live connection keeps
  working long after it expires, so there is no reason to reconnect when the
  token is refreshed;
* replies come back **asynchronously**, out of step with what was sent, so
  there is a single reader loop and nothing ever waits for a reply;
* ``unsubsPool`` answers 500, so closing the socket is how you stop everything.

The channel is an accelerator, never a source of truth: the REST poll keeps
running unchanged, and a push only shortens the wait for a value the next poll
would have brought anyway. That is what makes it safe to fail silently.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
import json
import logging
from typing import Any

import aiohttp

from ..utils import mask_device_id
from ._constants import FLUIDRA_USER_AGENT

_LOGGER = logging.getLogger(__name__)

WS_URL = "wss://ws.fluidra-emea.com"

# Well under the 600 s idle timeout measured on the gateway, and frequent
# enough that a dead connection is noticed within a poll cycle rather than at
# the next state change.
PING_INTERVAL = 240.0

# A closed socket is routine here (idle timeout, gateway recycling, network
# blips), so reconnection starts fast and backs off to something that will not
# hammer the cloud if it is down for an hour.
INITIAL_RECONNECT_DELAY = 5.0
MAX_RECONNECT_DELAY = 300.0
RECONNECT_MULTIPLIER = 2.0


@dataclass(frozen=True, slots=True)
class ComponentChange:
    """One ``componentChange`` frame, as the cloud pushes it."""

    device_id: str
    component_id: int
    reported_value: Any
    timestamp: int | None = None


def parse_frame(raw: Any) -> ComponentChange | None:
    """Turn one received frame into a ComponentChange, or None.

    None covers everything that is not a usable state change: subscription
    acknowledgements, the ``Forbidden`` reply to an unknown action, malformed
    payloads. The body is itself a JSON *string* inside the envelope, which is
    why it is decoded twice.
    """
    if isinstance(raw, (bytes, bytearray)):
        try:
            raw = raw.decode()
        except UnicodeDecodeError:
            return None
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (ValueError, TypeError):
            return None
    if not isinstance(raw, dict) or raw.get("action") != "componentChange":
        return None

    body = raw.get("body")
    if isinstance(body, str):
        try:
            body = json.loads(body)
        except (ValueError, TypeError):
            return None
    if not isinstance(body, dict):
        return None

    device_id = body.get("deviceId")
    if not isinstance(device_id, str) or not device_id:
        return None
    try:
        component_id = int(body["componentId"])
    except (KeyError, TypeError, ValueError):
        return None
    if "reportedValue" not in body:
        return None

    timestamp = body.get("ts")
    try:
        timestamp = int(timestamp) if timestamp is not None else None
    except (TypeError, ValueError):
        timestamp = None

    return ComponentChange(
        device_id=device_id,
        component_id=component_id,
        reported_value=body["reportedValue"],
        timestamp=timestamp,
    )


class FluidraWebsocketClient:
    """Keep one subscribed connection alive and hand changes to a callback.

    Owns nothing but its own task: the session and the token come from the API
    client, the devices to subscribe to are given at start, and what to do with
    a change is the caller's business.
    """

    def __init__(
        self,
        session: aiohttp.ClientSession,
        token_provider: Callable[[], str | None],
        on_change: Callable[[ComponentChange], Awaitable[None] | None],
        url: str = WS_URL,
    ) -> None:
        """Initialize the client. Nothing connects until :meth:`start`."""
        self._session = session
        self._token_provider = token_provider
        self._on_change = on_change
        self._url = url
        self._device_ids: list[str] = []
        self._task: asyncio.Task[None] | None = None
        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._stopping = False
        self.connected = False
        self.changes_received = 0
        self.reconnections = 0

    async def start(self, device_ids: list[str]) -> None:
        """Begin connecting, and keep the connection up until :meth:`stop`."""
        self._device_ids = list(device_ids)
        self._stopping = False
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        """Close the connection and stop reconnecting."""
        self._stopping = True
        if self._ws is not None and not self._ws.closed:
            await self._ws.close()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # shutdown must not raise
                pass
            self._task = None
        self.connected = False

    async def update_devices(self, device_ids: list[str]) -> None:
        """Subscribe to devices discovered after the connection was opened."""
        new = [device_id for device_id in device_ids if device_id not in self._device_ids]
        self._device_ids.extend(new)
        if not new or self._ws is None or self._ws.closed:
            return
        for device_id in new:
            await self._subscribe(self._ws, device_id)

    async def _subscribe(self, ws: aiohttp.ClientWebSocketResponse, device_id: str) -> None:
        """Send one subscription. The acknowledgement, if any, arrives later."""
        await ws.send_json({"action": "subsDevice", "deviceType": "connected", "deviceId": device_id})

    async def _run(self) -> None:
        """Connect, read, and reconnect with backoff until stopped."""
        delay = INITIAL_RECONNECT_DELAY
        while not self._stopping:
            try:
                await self._connect_once()
                # A clean return means the socket closed: reconnect promptly,
                # since the commonest cause is the 600 s idle timeout.
                delay = INITIAL_RECONNECT_DELAY
            except asyncio.CancelledError:
                raise
            except Exception as err:  # the channel must never break the integration
                _LOGGER.debug("Realtime channel error (%s), retrying in %.0fs", type(err).__name__, delay)
                delay = min(delay * RECONNECT_MULTIPLIER, MAX_RECONNECT_DELAY)

            if self._stopping:
                break
            self.connected = False
            self.reconnections += 1
            await asyncio.sleep(delay)

    async def _connect_once(self) -> None:
        """Open one connection and read it until it closes."""
        token = self._token_provider()
        if not token:
            raise RuntimeError("no access token available")

        headers = {"User-Agent": FLUIDRA_USER_AGENT}
        # autoping=True so aiohttp answers the gateway's own PING frames: the
        # read loop drops every non-text frame, so an unanswered PING would let
        # the gateway drop us. heartbeat stays None — the keepalive below is ours.
        async with self._session.ws_connect(
            f"{self._url}?token={token}", headers=headers, autoping=True, heartbeat=None
        ) as ws:
            self._ws = ws
            self.connected = True
            _LOGGER.debug("Realtime channel connected, subscribing to %d device(s)", len(self._device_ids))
            for device_id in self._device_ids:
                await self._subscribe(ws, device_id)

            pinger = asyncio.create_task(self._ping_loop(ws))
            try:
                async for message in ws:
                    if message.type is not aiohttp.WSMsgType.TEXT:
                        continue
                    await self._handle(message.data)
            finally:
                pinger.cancel()
                self._ws = None
                self.connected = False

    async def _ping_loop(self, ws: aiohttp.ClientWebSocketResponse) -> None:
        """Keep the connection under the gateway's idle timeout."""
        try:
            while not ws.closed:
                await asyncio.sleep(PING_INTERVAL)
                if ws.closed:
                    return
                await ws.ping(b"ha")
        except (asyncio.CancelledError, ConnectionResetError, aiohttp.ClientError):
            return

    async def _handle(self, data: Any) -> None:
        """Dispatch one frame, never letting a callback failure kill the reader."""
        change = parse_frame(data)
        if change is None:
            return
        self.changes_received += 1
        _LOGGER.debug(
            "Realtime change: device %s component %s = %s",
            mask_device_id(change.device_id),
            change.component_id,
            change.reported_value,
        )
        try:
            result = self._on_change(change)
            if asyncio.iscoroutine(result):
                await result
        except Exception:  # a bad callback must not close the channel
            _LOGGER.exception("Realtime change handler failed")
