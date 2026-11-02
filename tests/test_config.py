import pytest

from bot.config import DEFAULT_SYSTEM_PROMPT, Config, ConfigError


@pytest.fixture
def env(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)  # чтобы не подхватить настоящие .env и system_prompt.txt
    for name in ("BOT_MODEL", "BOT_EFFORT", "BOT_MAX_TOKENS", "HISTORY_MAX_MESSAGES", "SYSTEM_PROMPT_FILE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("VK_TOKEN", "vk-token")
    monkeypatch.setenv("VK_GROUP_ID", "club123")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "key")
    return monkeypatch


def test_defaults(env):
    config = Config.from_env(env_file=None)
    assert config.vk_group_id == 123
    assert config.claude_model == "claude-opus-5-5"
    assert config.claude_effort == "medium"
    assert config.system_prompt == DEFAULT_SYSTEM_PROMPT


def test_system_prompt_from_file(env, tmp_path):
    (tmp_path / "system_prompt.txt").write_text("Ты — бот пекарни.", encoding="utf-8")
    assert Config.from_env(env_file=None).system_prompt == "Ты — бот пекарни."


def test_missing_variables_are_listed(env):
    env.delenv("VK_TOKEN")
    env.delenv("ANTHROPIC_API_KEY")
    with pytest.raises(ConfigError, match="VK_TOKEN, ANTHROPIC_API_KEY"):
        Config.from_env(env_file=None)


@pytest.mark.parametrize(("name", "value"), [("BOT_EFFORT", "turbo"), ("VK_GROUP_ID", "abc")])
def test_invalid_values(env, name, value):
    env.setenv(name, value)
    with pytest.raises(ConfigError):
        Config.from_env(env_file=None)
