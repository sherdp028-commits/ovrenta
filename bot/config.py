"""Настройки бота: читаются из переменных окружения (и файла .env)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

DEFAULT_SYSTEM_PROMPT = (
    "Ты — дружелюбный помощник сообщества во ВКонтакте. "
    "Отвечай на языке собеседника, по делу и без лишней воды. "
    "Пиши обычным текстом: ВКонтакте не отображает Markdown, поэтому не используй "
    "звёздочки, решётки и другую разметку. Если не знаешь ответа, честно скажи об этом."
)

ALLOWED_EFFORTS = ("low", "medium", "high", "xhigh", "max")


class ConfigError(Exception):
    """Не хватает настроек или они заданы неверно."""


@dataclass(frozen=True)
class Config:
    vk_token: str
    vk_group_id: int
    vk_api_version: str = "5.199"
    claude_model: str = "claude-opus-5-5"
    claude_effort: str = "medium"
    claude_max_tokens: int = 16000
    system_prompt: str = DEFAULT_SYSTEM_PROMPT
    history_max_messages: int = 40
    log_level: str = "INFO"

    @classmethod
    def from_env(cls, env_file: str | None = ".env") -> "Config":
        if env_file:
            load_dotenv(env_file)

        vk_token = os.getenv("VK_TOKEN", "").strip()
        group_id_raw = os.getenv("VK_GROUP_ID", "").strip()
        missing = [
            name
            for name, value in (
                ("VK_TOKEN", vk_token),
                ("VK_GROUP_ID", group_id_raw),
                ("ANTHROPIC_API_KEY", os.getenv("ANTHROPIC_API_KEY", "").strip()),
            )
            if not value
        ]
        if missing:
            raise ConfigError(
                "Не заданы переменные окружения: "
                + ", ".join(missing)
                + ". Скопируйте .env.example в .env и заполните его."
            )

        effort = os.getenv("BOT_EFFORT", cls.claude_effort).strip().lower()
        if effort not in ALLOWED_EFFORTS:
            raise ConfigError(
                f"BOT_EFFORT={effort!r}: допустимые значения — {', '.join(ALLOWED_EFFORTS)}"
            )

        history_max = _int_env("HISTORY_MAX_MESSAGES", cls.history_max_messages)
        if history_max < 2:
            raise ConfigError("HISTORY_MAX_MESSAGES должно быть не меньше 2")

        return cls(
            vk_token=vk_token,
            # ID сообщества пишут и как «123», и как «-123», и как «club123».
            vk_group_id=_parse_group_id(group_id_raw),
            vk_api_version=os.getenv("VK_API_VERSION", cls.vk_api_version).strip(),
            claude_model=os.getenv("BOT_MODEL", cls.claude_model).strip(),
            claude_effort=effort,
            claude_max_tokens=_int_env("BOT_MAX_TOKENS", cls.claude_max_tokens),
            system_prompt=_load_system_prompt(),
            history_max_messages=history_max,
            log_level=os.getenv("LOG_LEVEL", cls.log_level).strip().upper(),
        )


def _int_env(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        raise ConfigError(f"{name} должно быть целым числом, а не {raw!r}") from None


def _parse_group_id(raw: str) -> int:
    cleaned = raw.lower().removeprefix("club").removeprefix("public").lstrip("-")
    if not cleaned.isdigit():
        raise ConfigError(f"VK_GROUP_ID должен быть числом (ID сообщества), а не {raw!r}")
    return int(cleaned)


def _load_system_prompt() -> str:
    """Промпт берётся из SYSTEM_PROMPT_FILE (по умолчанию system_prompt.txt)."""
    path = Path(os.getenv("SYSTEM_PROMPT_FILE", "system_prompt.txt"))
    if path.is_file():
        text = path.read_text(encoding="utf-8").strip()
        if text:
            return text
    return DEFAULT_SYSTEM_PROMPT
