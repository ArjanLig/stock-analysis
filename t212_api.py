"""
Trading 212 read-only broker client.

Fetches Invest-account positions and cash from the Trading 212 Public API
(beta) and normalises them into the app's portfolio contract. Read-only:
no order/write endpoints are called. Live environment only.
"""

import base64
import logging
import time  # noqa: F401

import requests  # noqa: F401

logger = logging.getLogger(__name__)

LIVE_BASE_URL = "https://live.trading212.com/api/v0"


def _auth_header(creds: dict) -> dict:
    """Build the HTTP Basic auth header from key+secret."""
    raw = f"{creds['t212_api_key']}:{creds['t212_api_secret']}".encode()
    return {"Authorization": "Basic " + base64.b64encode(raw).decode()}
