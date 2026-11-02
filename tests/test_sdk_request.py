"""Проверяем, что настоящий SDK Anthropic собирает корректный запрос (без сети)."""

import json

import anthropic
import httpx2

from bot.assistant import FALLBACK_BETA, Assistant


async def test_real_sdk_builds_expected_request(monkeypatch):
    captured = []

    def handler(request):
        captured.append(request)
        body = {
            "id": "msg_1",
            "type": "message",
            "role": "assistant",
            "model": "claude-opus-5-5",
            "content": [
                {"type": "thinking", "thinking": "", "signature": "sig"},
                {"type": "text", "text": "Привет!"},
            ],
            "stop_reason": "end_turn",
            "stop_sequence": None,
            "usage": {"input_tokens": 10, "output_tokens": 5},
        }
        return httpx2.Response(200, json=body)

    client = anthropic.AsyncAnthropic(
        api_key="test-key",
        http_client=anthropic.DefaultAsyncHttpxClient(transport=httpx2.MockTransport(handler)),
        max_retries=0,
    )
    assistant = Assistant(
        client,
        model="claude-opus-5-5",
        effort="medium",
        max_tokens=16000,
        system_prompt="system",
        history_max_messages=40,
    )

    assert await assistant.ask(7, "Привет") == "Привет!"
    assert await assistant.ask(7, "Ещё раз") == "Привет!"

    request = captured[-1]
    assert request.url.path == "/v1/messages"
    assert FALLBACK_BETA in request.headers["anthropic-beta"]
    body = json.loads(request.content)
    assert body["model"] == "claude-opus-5-5"
    assert body["fallbacks"] == "default"
    assert body["output_config"] == {"effort": "medium"}
    assert body["cache_control"] == {"type": "ephemeral"}
    assert "thinking" not in body  # на Claude Opus 5.5 размышления включены всегда
    assert body["messages"][1]["content"][0] == {
        "type": "thinking",
        "thinking": "",
        "signature": "sig",
    }
    await client.close()
