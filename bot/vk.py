"""Минимальный асинхронный клиент VK API для бота сообщества (Bots Long Poll API)."""

from __future__ import annotations

import asyncio
import logging
import secrets
from collections.abc import AsyncIterator
from typing import Any

import httpx

log = logging.getLogger(__name__)

API_URL = "https://api.vk.com/method/"
# Ограничение VK на длину одного сообщения — 4096 символов; берём с запасом.
MAX_MESSAGE_LENGTH = 4000
LONG_POLL_WAIT = 25  # секунд, максимум для Bots Long Poll API
TOO_MANY_REQUESTS = 6


class VKError(Exception):
    def __init__(self, code: int, message: str):
        super().__init__(f"VK API error {code}: {message}")
        self.code = code
        self.message = message


def split_text(text: str, limit: int = MAX_MESSAGE_LENGTH) -> list[str]:
    """Режет длинный текст на части, стараясь не рвать абзацы и слова."""
    text = text.strip()
    parts: list[str] = []
    while len(text) > limit:
        cut = text.rfind("\n", 0, limit)
        if cut < limit // 2:
            cut = text.rfind(" ", 0, limit)
        if cut < limit // 2:
            cut = limit
        parts.append(text[:cut].rstrip())
        text = text[cut:].lstrip()
    if text:
        parts.append(text)
    return parts


class VKClient:
    def __init__(
        self,
        token: str,
        group_id: int,
        api_version: str = "5.199",
        http: httpx.AsyncClient | None = None,
    ):
        self._token = token
        self.group_id = group_id
        self._version = api_version
        self._http = http or httpx.AsyncClient(timeout=httpx.Timeout(LONG_POLL_WAIT + 10))

    async def aclose(self) -> None:
        await self._http.aclose()

    async def call(self, method: str, **params: Any) -> Any:
        """Вызывает метод VK API и возвращает поле response."""
        data = {**params, "access_token": self._token, "v": self._version}
        for attempt in range(3):
            resp = await self._http.post(API_URL + method, data=data)
            resp.raise_for_status()
            body = resp.json()
            error = body.get("error")
            if not error:
                return body.get("response")
            code = error.get("error_code", 0)
            if code == TOO_MANY_REQUESTS and attempt < 2:
                await asyncio.sleep(1 + attempt)
                continue
            raise VKError(code, error.get("error_msg", ""))
        raise AssertionError("unreachable")

    async def send_message(self, peer_id: int, text: str) -> None:
        for part in split_text(text):
            await self.call(
                "messages.send",
                peer_id=peer_id,
                message=part,
                random_id=secrets.randbits(31),
            )

    async def set_typing(self, peer_id: int) -> None:
        await self.call(
            "messages.setActivity", peer_id=peer_id, type="typing", group_id=self.group_id
        )

    async def listen(self) -> AsyncIterator[dict[str, Any]]:
        """Бесконечно получает события сообщества через Bots Long Poll API."""
        server = await self._get_long_poll_server()
        log.info("Подключился к Long Poll сообщества %s", self.group_id)
        ts = server["ts"]
        while True:
            try:
                resp = await self._http.get(
                    server["server"],
                    params={
                        "act": "a_check",
                        "key": server["key"],
                        "ts": ts,
                        "wait": LONG_POLL_WAIT,
                    },
                )
                resp.raise_for_status()
                body = resp.json()
            except (httpx.HTTPError, ValueError) as exc:
                log.warning("Long Poll: ошибка соединения (%s), повтор через 3 с", exc)
                await asyncio.sleep(3)
                continue

            failed = body.get("failed")
            if failed == 1:  # история событий устарела — берём новый ts
                ts = body["ts"]
                continue
            if failed in (2, 3):  # истёк ключ (2) или потеряна информация (3)
                log.info("Long Poll: обновляю ключ (failed=%s)", failed)
                fresh = await self._get_long_poll_server()
                server["server"], server["key"] = fresh["server"], fresh["key"]
                if failed == 3:
                    ts = fresh["ts"]
                continue
            if failed is not None:
                log.warning("Long Poll: неизвестный ответ %r", body)
                await asyncio.sleep(1)
                continue

            ts = body.get("ts", ts)
            for update in body.get("updates", []):
                yield update

    async def _get_long_poll_server(self) -> dict[str, Any]:
        """Получает адрес и ключ Long Poll, переживая временные сбои сети.

        Ошибки VK API (неверный токен, выключенный Long Poll) пробрасываются
        сразу — их нужно исправлять в настройках, а не ждать.
        """
        delay = 3
        while True:
            try:
                return await self.call("groups.getLongPollServer", group_id=self.group_id)
            except httpx.HTTPError as exc:
                log.warning("Нет связи с VK API (%s), повтор через %s с", exc, delay)
                await asyncio.sleep(delay)
                delay = min(delay * 2, 60)
