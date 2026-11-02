"""Точка входа: python -m bot"""

from __future__ import annotations

import asyncio
import logging
import sys

import anthropic

from .app import Bot
from .assistant import Assistant
from .config import Config, ConfigError
from .vk import VKClient, VKError


async def main(config: Config) -> None:
    vk = VKClient(config.vk_token, config.vk_group_id, config.vk_api_version)
    async with anthropic.AsyncAnthropic() as claude:
        assistant = Assistant(
            claude,
            model=config.claude_model,
            effort=config.claude_effort,
            max_tokens=config.claude_max_tokens,
            system_prompt=config.system_prompt,
            history_max_messages=config.history_max_messages,
        )
        try:
            await Bot(vk, assistant).run()
        finally:
            await vk.aclose()


def run() -> None:
    try:
        config = Config.from_env()
    except ConfigError as exc:
        print(f"Ошибка настройки: {exc}", file=sys.stderr)
        sys.exit(1)

    logging.basicConfig(
        level=config.log_level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpx2").setLevel(logging.WARNING)
    logging.info("Модель: %s, effort: %s", config.claude_model, config.claude_effort)

    try:
        asyncio.run(main(config))
    except KeyboardInterrupt:
        logging.info("Бот остановлен")
    except VKError as exc:
        logging.critical(
            "Ошибка VK API: %s. Проверьте VK_TOKEN, VK_GROUP_ID и что в сообществе "
            "включён Long Poll API (см. README).",
            exc,
        )
        sys.exit(1)


if __name__ == "__main__":
    run()
