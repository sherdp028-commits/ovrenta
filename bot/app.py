"""Обработка входящих сообщений ВКонтакте."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import re
from typing import Any, Protocol

log = logging.getLogger(__name__)

# peer_id бесед начинаются с этого числа; всё, что меньше, — личные сообщения.
CHAT_PEER_OFFSET = 2_000_000_000
TYPING_REFRESH_SECONDS = 4  # индикатор «печатает» в VK гаснет примерно через 5 секунд

START_COMMANDS = {"начать", "старт", "start", "/start"}
RESET_COMMANDS = {"/reset", "/сброс", "сброс", "новый диалог"}
HELP_COMMANDS = {"/help", "/помощь", "помощь"}

GREETING_TEXT = (
    "Привет! Я бот с искусственным интеллектом. Задайте любой вопрос — постараюсь помочь.\n\n"
    "Я помню наш разговор. Чтобы начать заново, напишите «сброс»."
)
HELP_TEXT = (
    "Просто напишите вопрос — я отвечу.\n\n"
    "Команды:\n"
    "сброс — забыть переписку и начать диалог заново\n"
    "помощь — показать это сообщение"
)
RESET_TEXT = "Готово, начинаем с чистого листа. О чём поговорим?"
TEXT_ONLY_TEXT = "Пока я понимаю только текстовые сообщения. Напишите вопрос словами."


class VKApi(Protocol):
    group_id: int

    async def send_message(self, peer_id: int, text: str) -> None: ...
    async def set_typing(self, peer_id: int) -> None: ...
    def listen(self) -> Any: ...


class Chat(Protocol):
    async def ask(self, chat_id: int, text: str) -> str: ...
    def reset(self, chat_id: int) -> None: ...


class Bot:
    def __init__(self, vk: VKApi, assistant: Chat):
        self._vk = vk
        self._assistant = assistant
        self._tasks: set[asyncio.Task[None]] = set()
        group_id = vk.group_id
        # Упоминание бота в беседе: «[club123|Название], ...» или «@club123».
        self._mention = re.compile(
            rf"\[(?:club|public){group_id}\|[^\]]*\][,:\s]*|@(?:club|public){group_id}\b[,:\s]*",
            re.IGNORECASE,
        )

    async def run(self) -> None:
        log.info("Бот запущен, жду сообщения")
        async for update in self._vk.listen():
            # Каждое сообщение обрабатываем в отдельной задаче, чтобы долгий
            # ответ одному человеку не задерживал остальных.
            task = asyncio.create_task(self._handle_safely(update))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)

    async def _handle_safely(self, update: dict[str, Any]) -> None:
        try:
            await self.handle_update(update)
        except Exception:
            log.exception("Ошибка при обработке события %s", update.get("type"))

    async def handle_update(self, update: dict[str, Any]) -> None:
        if update.get("type") != "message_new":
            return
        message = update.get("object", {}).get("message", {})
        peer_id = message.get("peer_id")
        from_id = message.get("from_id", 0)
        if not peer_id or from_id <= 0:  # from_id < 0 — сообщение от другого сообщества
            return

        text = (message.get("text") or "").strip()
        is_group_chat = peer_id >= CHAT_PEER_OFFSET

        if is_group_chat:
            # В беседах отвечаем только когда к боту обратились по имени.
            stripped = self._mention.sub("", text, count=1).strip()
            if stripped == text:
                return
            text = stripped

        command = text.lower().rstrip("!.")
        if _payload_command(message) == "start" or command in START_COMMANDS:
            await self._vk.send_message(peer_id, GREETING_TEXT)
            return
        if command in RESET_COMMANDS:
            self._assistant.reset(peer_id)
            await self._vk.send_message(peer_id, RESET_TEXT)
            return
        if command in HELP_COMMANDS:
            await self._vk.send_message(peer_id, HELP_TEXT)
            return
        if not text:
            if message.get("attachments") or message.get("fwd_messages"):
                await self._vk.send_message(peer_id, TEXT_ONLY_TEXT)
            return

        await self._show_typing(peer_id)
        typing = asyncio.create_task(self._keep_typing(peer_id))
        try:
            reply = await self._assistant.ask(peer_id, text)
        finally:
            typing.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await typing
        await self._vk.send_message(peer_id, reply)

    async def _show_typing(self, peer_id: int) -> bool:
        try:
            await self._vk.set_typing(peer_id)
        except Exception as exc:  # индикатор не критичен — просто пишем в лог
            log.debug("Не удалось показать «печатает»: %s", exc)
            return False
        return True

    async def _keep_typing(self, peer_id: int) -> None:
        """Продлевает индикатор «печатает», пока Claude готовит ответ."""
        while True:
            await asyncio.sleep(TYPING_REFRESH_SECONDS)
            if not await self._show_typing(peer_id):
                return


def _payload_command(message: dict[str, Any]) -> str | None:
    """Кнопка «Начать» присылает payload вида {"command": "start"}."""
    payload = message.get("payload")
    if not payload:
        return None
    try:
        data = json.loads(payload)
    except (TypeError, ValueError):
        return None
    return data.get("command") if isinstance(data, dict) else None
