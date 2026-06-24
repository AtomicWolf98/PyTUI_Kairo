"""Tests for provider health check. Network hits are skipped in CI."""
from __future__ import annotations

import io
import unittest
import urllib.error
from unittest.mock import MagicMock

from agent.provider_health import (
    STATUS_AUTH_ERROR,
    STATUS_MODEL_ERROR,
    STATUS_RATE_LIMIT,
    STATUS_SUCCESS,
    STATUS_URL_ERROR,
    STATUS_UNKNOWN,
)
# Access the probe function via the module alias to avoid pytest collecting the
# imported `test_connection` symbol as if it were a test function itself.
from agent import provider_health as _ph


def probe(**kwargs):
    return _ph.test_connection(**kwargs)


def _fake_response(status: int, body: bytes = b""):
    response = MagicMock()
    response.status = status
    response.read.return_value = body
    response.close = MagicMock()
    return response


def _http_error(code: int, body: bytes = b""):
    err = urllib.error.HTTPError(
        url="https://example.com/v1/chat/completions",
        code=code,
        msg="error",
        hdrs=None,
        fp=io.BytesIO(body),
    )
    return err


class TestProviderHealth(unittest.TestCase):
    def test_success_classified(self):
        opener = MagicMock()
        opener.open.return_value = _fake_response(200, b'{"id":"x"}')
        result = probe(
            base_url="https://api.example.com/v1",
            api_key="sk-test",
            model="model-x",
            opener=opener,
        )
        self.assertEqual(result.status, STATUS_SUCCESS)
        self.assertEqual(result.http_status, 200)
        self.assertTrue(result.summary().startswith("[OK]"))

    def test_auth_error_for_401(self):
        opener = MagicMock()
        opener.open.side_effect = _http_error(401, b'{"error":{"message":"invalid api key"}}')
        result = probe(
            base_url="https://api.example.com/v1",
            api_key="sk-wrong",
            model="model-x",
            opener=opener,
        )
        self.assertEqual(result.status, STATUS_AUTH_ERROR)
        self.assertEqual(result.http_status, 401)
        self.assertIn("[Auth Error]", result.summary())

    def test_model_error_for_404(self):
        opener = MagicMock()
        opener.open.side_effect = _http_error(404, b'{"error":{"message":"model not found"}}')
        result = probe(
            base_url="https://api.example.com/v1",
            api_key="sk-test",
            model="model-x",
            opener=opener,
        )
        self.assertEqual(result.status, STATUS_MODEL_ERROR)

    def test_model_error_for_model_marker_in_body(self):
        opener = MagicMock()
        opener.open.side_effect = _http_error(400, b'{"error":{"message":"Model does not exist"}}')
        result = probe(
            base_url="https://api.example.com/v1",
            api_key="sk-test",
            model="model-x",
            opener=opener,
        )
        self.assertEqual(result.status, STATUS_MODEL_ERROR)

    def test_rate_limit(self):
        opener = MagicMock()
        opener.open.side_effect = _http_error(429, b'{"error":{"message":"slow down"}}')
        result = probe(
            base_url="https://api.example.com/v1",
            api_key="sk-test",
            model="model-x",
            opener=opener,
        )
        self.assertEqual(result.status, STATUS_RATE_LIMIT)

    def test_url_error(self):
        import urllib.error as url_error

        opener = MagicMock()
        opener.open.side_effect = url_error.URLError("no host")
        result = probe(
            base_url="https://api.example.com/v1",
            api_key="sk-test",
            model="model-x",
            opener=opener,
        )
        self.assertEqual(result.status, STATUS_URL_ERROR)

    def test_invalid_scheme_returns_url_error_without_request(self):
        opener = MagicMock()
        result = probe(
            base_url="not-a-url",
            api_key="",
            model="m",
            opener=opener,
        )
        self.assertEqual(result.status, STATUS_URL_ERROR)
        opener.open.assert_not_called()

    def test_url_append_chat_completions(self):
        opener = MagicMock()
        opener.open.return_value = _fake_response(200, b"{}")
        probe(
            base_url="https://api.example.com/v1",
            api_key="k",
            model="m",
            opener=opener,
        )
        called_url = opener.open.call_args[0][0].full_url
        self.assertTrue(called_url.endswith("/chat/completions"))

    def test_payload_has_no_tools_and_no_stream(self):
        opener = MagicMock()
        opener.open.return_value = _fake_response(200, b"{}")
        probe(
            base_url="https://api.example.com/v1",
            api_key="k",
            model="m",
            opener=opener,
        )
        request = opener.open.call_args[0][0]
        import json as _json

        payload = _json.loads(request.data.decode("utf-8"))
        self.assertFalse(payload.get("stream"))
        self.assertNotIn("tools", payload)
        self.assertEqual(payload["max_tokens"], 1)
        self.assertEqual(payload["temperature"], 0)
        self.assertEqual(payload["messages"][0]["content"], "ping")

    def test_unknown_status_for_other_4xx(self):
        opener = MagicMock()
        opener.open.side_effect = _http_error(418, b'{"error":{"message":"im a teapot"}}')
        result = probe(
            base_url="https://api.example.com/v1",
            api_key="k",
            model="m",
            opener=opener,
        )
        self.assertEqual(result.status, STATUS_UNKNOWN)


if __name__ == "__main__":
    unittest.main()