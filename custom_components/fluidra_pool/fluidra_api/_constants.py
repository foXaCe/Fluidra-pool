"""Shared constants for the Fluidra Pool API client."""

from __future__ import annotations

from typing import Final

FLUIDRA_EMEA_BASE: Final = "https://api.fluidra-emea.com"
COGNITO_ENDPOINT: Final = "https://cognito-idp.eu-west-1.amazonaws.com/"
COGNITO_CLIENT_ID: Final = "g3njunelkcbtefosqm9bdhhq1"

# Fluidra EMEA endpoints (single source — several mixins hit the same paths).
USER_POOLS_ENDPOINT: Final = f"{FLUIDRA_EMEA_BASE}/generic/users/me/pools"
DEVICES_ENDPOINT: Final = f"{FLUIDRA_EMEA_BASE}/generic/devices"
CONSUMER_PROFILE_ENDPOINT: Final = f"{FLUIDRA_EMEA_BASE}/mobile/consumers/me"
# Query parameter required by the device/component endpoints.
CONNECTED_PARAMS: Final[dict[str, str]] = {"deviceType": "connected"}
FLUIDRA_USER_AGENT: Final = (
    "com.fluidra.iaqualinkplus/1741857021 "
    "(Linux; U; Android 14; fr_FR; MI PAD 4; Build/UQ1A.240205.004; Cronet/140.0.7289.0)"
)
RETRYABLE_STATUSES: Final[frozenset[int]] = frozenset({429, 500, 502, 503, 504})
MAX_REFRESH_ATTEMPTS: Final = 1
