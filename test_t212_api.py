"""
Unit tests for t212_api.py — the read-only Trading 212 broker client.

All HTTP is mocked; tests run without network access or real credentials.
"""

import base64
import unittest
from unittest.mock import MagicMock, patch  # noqa: F401

import t212_api


_CREDS = {"t212_api_key": "KEY123", "t212_api_secret": "SECRET456"}


class TestAuthHeader(unittest.TestCase):
    def test_auth_header_is_basic_base64_key_colon_secret(self):
        header = t212_api._auth_header(_CREDS)
        expected = base64.b64encode(b"KEY123:SECRET456").decode()
        self.assertEqual(header["Authorization"], f"Basic {expected}")
