from types import SimpleNamespace

import anthropic
import httpx2
import pytest

from bot.assistant import (
    ERROR_TEXT,
    FALLBACK_BETA,
    RATE_LIMIT_TEXT,
    REFUSAL_TEXT,
    Assistant,
    content_for_history,
    trim_history,
)


def text(value):
    return SimpleNamespace(type="text", text=value)


def thinking(sig="sig"):
    return SimpleNamespace(type="thinking", thinking="", signature=sig)


def response(*blocks, stop_reason="end_turn"):
    return SimpleNamespace(content=list(blocks), stop_reason=stop_reason, stop_details=None)


class FakeMessages:
    def __init__(self, *results):
        self.results = list(results)
        self.calls = []

    async def create(self, **kwargs):
        # Копируем список: сам ассистент потом дописывает историю.
        self.calls.append({**kwargs, "messages": list(kwargs["messages"])})
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


def make_assistant(*results, history_max=40):
    messages = FakeMessages(*results)
    client = SimpleNamespace(beta=SimpleNamespace(messages=messages))
    assistant = Assistant(
        client,
        model="claude-opus-5-5",
        effort="medium",
        max_tokens=16000,
        system_prompt="system",
        history_max_messages=history_max,
    )
    return assistant, messages


def api_error(cls, status):
    request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    return cls("error", response=httpx2.Response(status, request=request), body=None)


async def test_ask_sends_request_and_remembers_dialog():
    assistant, api = make_assistant(
        response(thinking("s1"), text("Привет, Аня!")),
        response(text("Тебя зовут Аня.")),
    )

    assert await assistant.ask(1, "Меня зовут Аня") == "Привет, Аня!"
    assert await assistant.ask(1, "Как меня зовут?") == "Тебя зовут Аня."

    first, second = api.calls
    assert first["model"] == "claude-opus-5-5"
    assert first["system"] == "system"
    assert first["output_config"] == {"effort": "medium"}
    assert first["betas"] == [FALLBACK_BETA]
    assert first["fallbacks"] == "default"
    assert first["messages"] == [{"role": "user", "content": "Меня зовут Аня"}]
    # Второй запрос несёт всю историю, включая блок размышлений без изменений.
    assert second["messages"] == [
        {"role": "user", "content": "Меня зовут Аня"},
        {
            "role": "assistant",
            "content": [
                {"type": "thinking", "thinking": "", "signature": "s1"},
                {"type": "text", "text": "Привет, Аня!"},
            ],
        },
        {"role": "user", "content": "Как меня зовут?"},
    ]


async def test_histories_are_separate_per_chat_and_reset_works():
    assistant, _ = make_assistant(response(text("a")), response(text("b")))
    await assistant.ask(1, "q1")
    await assistant.ask(2, "q2")
    assert len(assistant.history(1)) == 2
    assert len(assistant.history(2)) == 2

    assistant.reset(1)
    assert assistant.history(1) == []
    assert len(assistant.history(2)) == 2


async def test_refusal_is_not_saved_to_history():
    assistant, _ = make_assistant(response(stop_reason="refusal"))
    assert await assistant.ask(1, "что-то запрещённое") == REFUSAL_TEXT
    assert assistant.history(1) == []


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (api_error(anthropic.RateLimitError, 429), RATE_LIMIT_TEXT),
        (api_error(anthropic.AuthenticationError, 401), ERROR_TEXT),
        (api_error(anthropic.InternalServerError, 500), ERROR_TEXT),
    ],
)
async def test_api_errors_give_friendly_text_and_keep_history(error, expected):
    assistant, _ = make_assistant(error)
    assert await assistant.ask(1, "привет") == expected
    assert assistant.history(1) == []


def test_content_for_history_drops_internal_blocks_before_fallback():
    blocks = [
        thinking("declined"),
        text("начало "),
        SimpleNamespace(type="fallback"),
        thinking("rescue"),
        text("конец"),
    ]
    assert content_for_history(blocks) == [
        {"type": "text", "text": "начало "},
        {"type": "thinking", "thinking": "", "signature": "rescue"},
        {"type": "text", "text": "конец"},
    ]


def test_trim_history_keeps_recent_pairs_without_thinking():
    history = []
    for i in range(6):
        history.append({"role": "user", "content": f"q{i}"})
        history.append(
            {
                "role": "assistant",
                "content": [
                    {"type": "thinking", "thinking": "", "signature": f"s{i}"},
                    {"type": "text", "text": f"a{i}"},
                ],
            }
        )

    assert trim_history(history, 12) is history  # в пределах лимита — без изменений

    trimmed = trim_history(history, 8)
    assert [m["role"] for m in trimmed] == ["user", "assistant"] * 2
    assert trimmed[0]["content"] == "q4"
    assert trimmed[1]["content"] == [{"type": "text", "text": "a4"}]
    assert trimmed[3]["content"] == [{"type": "text", "text": "a5"}]
