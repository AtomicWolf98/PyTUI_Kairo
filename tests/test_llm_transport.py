import json
from io import BytesIO
from unittest import TestCase
from unittest.mock import patch
import urllib.error

from agent.config import Config
from agent.llm import LLMClient


class StubResponse:
    def __init__(self, body_bytes: bytes, status: int = 200, headers=None):
        self.body = BytesIO(body_bytes)
        self.code = status
        self.headers = headers or {}

    def read(self, size=-1):
        return self.body.read(size)

    def readline(self):
        return self.body.readline()

    def close(self):
        self.body.close()

    def __iter__(self):
        return iter(self.body.readlines())


def _http_error(body_bytes: bytes, status: int):
    return urllib.error.HTTPError(
        url="https://test.example.com/v1/chat/completions",
        code=status,
        msg="error",
        hdrs={},
        fp=BytesIO(body_bytes),
    )


class TestLLMClientErrors(TestCase):
    def setUp(self):
        self.temp_config_path = "config.json"
        self.config = Config(config_path=self.temp_config_path)
        self.config.base_url = "https://test.example.com/v1"
        self.config.api_key = "test-key"
        self.config.model = "test-model"
        self.config.max_tokens = 100
        self.config.context_window = 4000
        self.config.temperature = 0.2
        self.client = LLMClient(self.config)

    def _make_sse(self, events):
        lines = []
        for event in events:
            lines.append(f"data: {json.dumps(event)}".encode("utf-8"))
        lines.append(b"data: [DONE]")
        return b"\n".join(lines) + b"\n"

    def test_classifies_context_error(self):
        error = _http_error(
            json.dumps({"error": {"message": "context length exceeded"}}).encode("utf-8"),
            400,
        )
        with patch.object(self.client._opener, "open", side_effect=error):
            events = list(self.client.stream_response([{"role": "user", "content": "hi"}]))
        self.assertEqual(events, [("context_error", "HTTP Error 400: context length exceeded")])

    def test_classifies_rate_limit_and_retries(self):
        errors = [
            _http_error(
                json.dumps({"error": {"message": "rate limit"}}).encode("utf-8"),
                429,
            ),
        ]
        success = StubResponse(
            self._make_sse([{"choices": [{"delta": {"content": "ok"}}]}]),
            status=200,
        )

        def side_effect(*args, **kwargs):
            if errors:
                raise errors.pop(0)
            return success

        with patch.object(self.client._opener, "open", side_effect=side_effect) as mock_open:
            with patch("agent.llm.time.sleep"):
                events = list(self.client.stream_response([{"role": "user", "content": "hi"}]))

        self.assertGreaterEqual(mock_open.call_count, 2)
        self.assertEqual(events, [("content", "ok")])

    def test_classifies_auth_error(self):
        error = _http_error(
            json.dumps({"error": {"message": "invalid api key"}}).encode("utf-8"),
            401,
        )
        with patch.object(self.client._opener, "open", side_effect=error):
            events = list(self.client.stream_response([{"role": "user", "content": "hi"}]))
        self.assertEqual(events, [("error", "HTTP Error 401: invalid api key")])

    def test_parses_sse_stream(self):
        sse = self._make_sse([
            {"choices": [{"delta": {"content": "Hello"}}]},
            {"choices": [{"delta": {"content": " world"}}]},
        ])
        response = StubResponse(sse)
        with patch.object(self.client._opener, "open", return_value=response):
            events = list(self.client.stream_response([{"role": "user", "content": "hi"}]))
        self.assertEqual(events, [("content", "Hello"), ("content", " world")])

    def test_parses_tool_calls(self):
        sse = self._make_sse([
            {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "call_1", "function": {"name": "read_file"}}]}}]},
            {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"arguments": '{"path": "x"}'}}]}}]},
        ])
        response = StubResponse(sse)
        with patch.object(self.client._opener, "open", return_value=response):
            events = list(self.client.stream_response([{"role": "user", "content": "hi"}]))

        self.assertEqual(len(events), 2)
        self.assertEqual(events[0][0], "tool_calls")
        self.assertEqual(events[1][0], "tool_calls")
        final = events[1][1]
        self.assertEqual(final[0]["id"], "call_1")
        self.assertEqual(final[0]["function"]["name"], "read_file")
        self.assertEqual(final[0]["function"]["arguments"], '{"path": "x"}')

    def test_parses_reasoning_content(self):
        sse = self._make_sse([
            {"choices": [{"delta": {"reasoning_content": "thinking..."}}]},
            {"choices": [{"delta": {"content": "answer"}}]},
        ])
        response = StubResponse(sse)
        with patch.object(self.client._opener, "open", return_value=response):
            events = list(self.client.stream_response([{"role": "user", "content": "hi"}]))
        self.assertEqual(events, [("thought", "thinking..."), ("content", "answer")])
