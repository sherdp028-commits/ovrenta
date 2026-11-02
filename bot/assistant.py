"""Диалог с Claude: история переписки для каждого чата и запросы к API."""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from typing import Any

import anthropic

log = logging.getLogger(__name__)

# Если запрос отклонят фильтры безопасности, API само повторит его на подходящей
# запасной модели (режим "default" выбирает её по категории отказа).
FALLBACK_BETA = "server-side-fallback-2026-07-01"

THINKING_TYPES = ("thinking", "redacted_thinking")

REFUSAL_TEXT = "Извините, с этим запросом я помочь не могу. Попробуйте спросить по-другому."
EMPTY_TEXT = "Не получилось сформулировать ответ. Попробуйте переформулировать вопрос."
RATE_LIMIT_TEXT = "Сейчас слишком много запросов. Попробуйте через минуту."
ERROR_TEXT = "Не получилось получить ответ. Попробуйте ещё раз чуть позже."

Message = dict[str, Any]


class Assistant:
    def __init__(
        self,
        client: anthropic.AsyncAnthropic,
        *,
        model: str,
        effort: str,
        max_tokens: int,
        system_prompt: str,
        history_max_messages: int,
    ):
        self._client = client
        self._model = model
        self._effort = effort
        self._max_tokens = max_tokens
        self._system = system_prompt
        self._history_max = history_max_messages
        self._histories: dict[int, list[Message]] = {}
        # Сообщения одного чата обрабатываем строго по очереди, чтобы не путать историю.
        self._locks: defaultdict[int, asyncio.Lock] = defaultdict(asyncio.Lock)

    def history(self, chat_id: int) -> list[Message]:
        return list(self._histories.get(chat_id, []))

    def reset(self, chat_id: int) -> None:
        self._histories.pop(chat_id, None)

    async def ask(self, chat_id: int, text: str) -> str:
        """Отправляет сообщение пользователя Claude и возвращает текст ответа."""
        async with self._locks[chat_id]:
            history = self._histories.get(chat_id, [])
            user_message: Message = {"role": "user", "content": text}

            try:
                response = await self._client.beta.messages.create(
                    model=self._model,
                    max_tokens=self._max_tokens,
                    system=self._system,
                    messages=[*history, user_message],
                    output_config={"effort": self._effort},
                    cache_control={"type": "ephemeral"},
                    betas=[FALLBACK_BETA],
                    fallbacks="default",
                )
            except (anthropic.AuthenticationError, anthropic.PermissionDeniedError) as exc:
                log.critical("Claude API отклонил ключ — проверьте ANTHROPIC_API_KEY: %s", exc)
                return ERROR_TEXT
            except anthropic.RateLimitError as exc:
                log.warning("Claude API: превышен лимит запросов: %s", exc)
                return RATE_LIMIT_TEXT
            except anthropic.APIStatusError as exc:
                log.error(
                    "Claude API вернул ошибку %s (request_id=%s): %s",
                    exc.status_code,
                    exc.request_id,
                    exc.message,
                )
                return ERROR_TEXT
            except anthropic.APIConnectionError as exc:
                log.error("Нет связи с Claude API: %s", exc)
                return ERROR_TEXT

            if response.stop_reason == "refusal":
                details = response.stop_details
                log.info(
                    "Claude отказался отвечать (категория: %s)",
                    getattr(details, "category", None),
                )
                return REFUSAL_TEXT
            if response.stop_reason == "max_tokens":
                log.warning("Ответ обрезан по лимиту BOT_MAX_TOKENS=%s", self._max_tokens)

            reply = "".join(b.text for b in response.content if b.type == "text").strip()
            if not reply:
                return EMPTY_TEXT

            assistant_message: Message = {
                "role": "assistant",
                "content": content_for_history(response.content),
            }
            self._histories[chat_id] = trim_history(
                [*history, user_message, assistant_message], self._history_max
            )
            return reply


def content_for_history(blocks: list[Any]) -> list[dict[str, Any]]:
    """Готовит блоки ответа к повторной отправке в следующих запросах.

    Блоки размышлений (thinking) передаём обратно без изменений — так модель
    видит свои прошлые рассуждения. Если ответ дописывала запасная модель
    (в ответе есть блок fallback), то из части до последнего такого блока
    оставляем только текст, как требует API.
    """
    boundary = -1
    for index, block in enumerate(blocks):
        if block.type == "fallback":
            boundary = index

    result: list[dict[str, Any]] = []
    for index, block in enumerate(blocks):
        before_fallback = index < boundary
        if block.type == "text":
            result.append({"type": "text", "text": block.text})
        elif block.type == "thinking" and not before_fallback:
            result.append(
                {"type": "thinking", "thinking": block.thinking, "signature": block.signature}
            )
        elif block.type == "redacted_thinking" and not before_fallback:
            result.append({"type": "redacted_thinking", "data": block.data})
    return result


def trim_history(history: list[Message], max_messages: int) -> list[Message]:
    """Не даёт истории расти бесконечно.

    Когда сообщений становится больше max_messages, оставляем последнюю
    половину. Блоки размышлений в оставшихся ответах привязаны к удалённому
    началу переписки и после обрезки стали бы недействительными, поэтому их
    убираем — текст ответов остаётся. Режем сразу половину, а не по одному
    сообщению, чтобы это происходило редко и не мешало кэшированию.
    """
    if len(history) <= max_messages:
        return history

    keep = max(2, max_messages // 2)
    keep -= keep % 2  # история — пары «вопрос-ответ», первым должен идти вопрос
    trimmed: list[Message] = []
    for message in history[-keep:]:
        content = message["content"]
        if message["role"] == "assistant" and isinstance(content, list):
            content = [b for b in content if b.get("type") not in THINKING_TYPES]
        trimmed.append({**message, "content": content})
    return trimmed
