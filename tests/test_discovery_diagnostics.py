"""Discovery failures must be visible in the log (Issue #196).

A pool whose device list comes back empty and a pool whose device request was
refused look identical from Home Assistant: "total_devices: 0" and nothing in
the log. @oskarreiners987-commits reported exactly that, with a log containing
only the successful authentication line.
"""

from __future__ import annotations

import logging
from typing import Any
from unittest.mock import AsyncMock

import pytest

from custom_components.fluidra_pool.fluidra_api import FluidraPoolAPI


@pytest.fixture
def api() -> Any:
    client = FluidraPoolAPI("user@example.com", "pw")
    client._build_auth_headers = lambda: {"authorization": "Bearer x"}  # type: ignore[method-assign]
    return client


async def test_refused_discovery_is_logged_with_status(api: Any, caplog: Any) -> None:
    api._request = AsyncMock(return_value=(403, None, '{"message":"Forbidden"}'))

    with caplog.at_level(logging.WARNING):
        result = await api._discover_devices_for_pool("4136", {})

    assert result == []
    assert "rejected by Fluidra (HTTP 403)" in caplog.text
    assert "4136" in caplog.text


async def test_empty_device_list_says_so(api: Any, caplog: Any) -> None:
    """The #196 case: HTTP 200 with no equipment, previously silent."""
    api._request = AsyncMock(return_value=(200, [], ""))

    with caplog.at_level(logging.WARNING):
        result = await api._discover_devices_for_pool("4136", {})

    assert result == []
    assert "no equipment for pool 4136" in caplog.text
    # Points at the likely cause rather than just stating the fact.
    assert "official app" in caplog.text


async def test_unusable_payload_is_logged(api: Any, caplog: Any) -> None:
    api._request = AsyncMock(return_value=(200, "not-a-list", ""))

    with caplog.at_level(logging.WARNING):
        result = await api._discover_devices_for_pool("4136", {})

    assert result == []
    assert "unusable payload" in caplog.text


async def test_a_successful_discovery_stays_quiet(api: Any, caplog: Any) -> None:
    """No warning noise on the normal path."""
    api._request = AsyncMock(
        return_value=(200, [{"id": "CC1", "name": "Chlorinator", "family": "Chlorinators"}], ""),
    )

    with caplog.at_level(logging.WARNING):
        result = await api._discover_devices_for_pool("4136", {})

    assert len(result) == 1
    assert caplog.text == ""
