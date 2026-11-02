import json
from urllib.parse import parse_qs

import httpx
import pytest

from bot.vk import VKClient, VKError, split_text


def make_client(handler):
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return VKClient("token", 123, http=http)


def test_split_text_short_message_is_one_part():
    assert split_text("привет") == ["привет"]


def test_split_text_respects_limit_and_keeps_words():
    text = " ".join(["слово"] * 3000)
    parts = split_text(text, limit=100)
    assert all(len(p) <= 100 for p in parts)
    assert " ".join(parts) == text


def test_split_text_prefers_line_breaks():
    text = "а" * 60 + "\n" + "б" * 60
    assert split_text(text, limit=100) == ["а" * 60, "б" * 60]


async def test_call_returns_response_and_sends_token():
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["form"] = parse_qs(request.content.decode())
        return httpx.Response(200, json={"response": 42})

    vk = make_client(handler)
    assert await vk.call("messages.send", peer_id=5) == 42
    assert seen["url"].endswith("/method/messages.send")
    assert seen["form"]["access_token"] == ["token"]
    assert seen["form"]["peer_id"] == ["5"]


async def test_call_raises_vk_error():
    def handler(request):
        return httpx.Response(200, json={"error": {"error_code": 5, "error_msg": "auth failed"}})

    vk = make_client(handler)
    with pytest.raises(VKError) as info:
        await vk.call("groups.getLongPollServer", group_id=123)
    assert info.value.code == 5


async def test_send_message_splits_long_text():
    sent = []

    def handler(request):
        sent.append(parse_qs(request.content.decode())["message"][0])
        return httpx.Response(200, json={"response": 1})

    vk = make_client(handler)
    await vk.send_message(5, "x" * 9000)
    assert [len(m) for m in sent] == [4000, 4000, 1000]


async def test_listen_yields_updates_and_handles_failed_codes():
    polls = []
    server_requests = 0

    def handler(request):
        nonlocal server_requests
        if request.url.path.endswith("groups.getLongPollServer"):
            server_requests += 1
            return httpx.Response(
                200,
                json={"response": {"server": "https://lp.vk.test/x", "key": f"k{server_requests}", "ts": "10"}},
            )
        params = dict(request.url.params)
        polls.append((params["key"], params["ts"]))
        step = len(polls)
        if step == 1:
            return httpx.Response(200, json={"failed": 1, "ts": "20"})
        if step == 2:
            return httpx.Response(200, json={"failed": 2})
        return httpx.Response(200, json={"ts": "21", "updates": [{"type": "message_new"}]})

    vk = make_client(handler)
    stream = vk.listen()
    update = await anext(stream)
    await stream.aclose()

    assert update == {"type": "message_new"}
    assert polls == [("k1", "10"), ("k1", "20"), ("k2", "20")]
    assert json.dumps(update)
