import json

from bot.app import (
    GREETING_TEXT,
    HELP_TEXT,
    RESET_TEXT,
    TEXT_ONLY_TEXT,
    Bot,
)

GROUP_ID = 123


class FakeVK:
    group_id = GROUP_ID

    def __init__(self):
        self.sent = []
        self.typing = []

    async def send_message(self, peer_id, text):
        self.sent.append((peer_id, text))

    async def set_typing(self, peer_id):
        self.typing.append(peer_id)

    def listen(self):
        raise NotImplementedError


class FakeAssistant:
    def __init__(self):
        self.asked = []
        self.resets = []

    async def ask(self, chat_id, text):
        self.asked.append((chat_id, text))
        return f"ответ на: {text}"

    def reset(self, chat_id):
        self.resets.append(chat_id)


def message_new(text="", peer_id=10, from_id=10, **extra):
    return {
        "type": "message_new",
        "object": {"message": {"text": text, "peer_id": peer_id, "from_id": from_id, **extra}},
    }


def make_bot():
    vk, assistant = FakeVK(), FakeAssistant()
    return Bot(vk, assistant), vk, assistant


async def test_private_message_goes_to_claude():
    bot, vk, assistant = make_bot()
    await bot.handle_update(message_new("Как дела?"))
    assert assistant.asked == [(10, "Как дела?")]
    assert vk.sent == [(10, "ответ на: Как дела?")]
    assert vk.typing == [10]


async def test_start_button_payload_and_commands():
    bot, vk, assistant = make_bot()
    await bot.handle_update(message_new("Начать", payload=json.dumps({"command": "start"})))
    await bot.handle_update(message_new("Сброс"))
    await bot.handle_update(message_new("помощь"))
    assert vk.sent == [(10, GREETING_TEXT), (10, RESET_TEXT), (10, HELP_TEXT)]
    assert assistant.resets == [10]
    assert assistant.asked == []


async def test_attachment_without_text_gets_hint():
    bot, vk, assistant = make_bot()
    await bot.handle_update(message_new("", attachments=[{"type": "photo"}]))
    assert vk.sent == [(10, TEXT_ONLY_TEXT)]
    assert assistant.asked == []


async def test_group_chat_requires_mention():
    bot, vk, assistant = make_bot()
    chat = 2_000_000_001
    await bot.handle_update(message_new("просто болтаем", peer_id=chat))
    await bot.handle_update(message_new(f"[club{GROUP_ID}|Бот], сколько будет 2+2?", peer_id=chat))
    await bot.handle_update(message_new(f"@club{GROUP_ID} сброс", peer_id=chat))
    assert assistant.asked == [(chat, "сколько будет 2+2?")]
    assert assistant.resets == [chat]


async def test_ignores_other_events_and_communities():
    bot, vk, assistant = make_bot()
    await bot.handle_update({"type": "message_reply", "object": {}})
    await bot.handle_update(message_new("привет", from_id=-555))
    assert vk.sent == []
    assert assistant.asked == []
