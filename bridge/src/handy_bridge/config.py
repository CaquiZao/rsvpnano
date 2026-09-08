"""Load and validate the bridge's config.toml."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

VALID_BACKENDS = {"claude_cli", "anthropic_api", "ollama", "none"}


class ConfigError(Exception):
    """Raised when config.toml is missing a field or holds an invalid value."""


@dataclass(frozen=True)
class AsrConfig:
    handy_exe: Path
    model: str
    timeout_s: int


@dataclass(frozen=True)
class PostProcessConfig:
    enabled: bool
    backend: str
    model: str
    answer_tasks: bool = True


@dataclass(frozen=True)
class KanbanConfig:
    enabled: bool = True
    subfolder: str = "Quadros"


@dataclass(frozen=True)
class DigestConfig:
    enabled: bool = True
    weekday: int = 6   # 0 = segunda ... 6 = domingo
    hour: int = 19


@dataclass(frozen=True)
class TelegramConfig:
    enabled: bool = False
    token: str = ""
    chat_id: str = ""


@dataclass(frozen=True)
class Config:
    vault_path: Path
    inbox_folder: str
    audio_store: Path
    port: int
    asr: AsrConfig
    post_process: PostProcessConfig
    # Optional sections: an older config.toml without them keeps working.
    kanban: KanbanConfig = KanbanConfig()
    telegram: TelegramConfig = TelegramConfig()
    digest: DigestConfig = DigestConfig()

    @property
    def inbox_path(self) -> Path:
        return self.vault_path / self.inbox_folder


def _require(table: dict, key: str, where: str):
    if key not in table:
        raise ConfigError(f"missing '{key}' in {where}")
    return table[key]


def load_config(path: Path) -> Config:
    try:
        raw = tomllib.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ConfigError(f"config file not found: {path}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"invalid TOML in {path}: {exc}") from exc

    vault_path = Path(_require(raw, "vault_path", "config")).expanduser()
    if not vault_path.is_dir():
        raise ConfigError(f"vault_path does not exist: {vault_path}")

    audio_store = Path(_require(raw, "audio_store", "config")).expanduser()
    audio_store.mkdir(parents=True, exist_ok=True)

    asr_raw = _require(raw, "asr", "config")
    pp_raw = _require(raw, "post_process", "config")

    backend = _require(pp_raw, "backend", "[post_process]")
    if backend not in VALID_BACKENDS:
        raise ConfigError(
            f"unknown backend '{backend}'; expected one of {sorted(VALID_BACKENDS)}"
        )

    kanban_raw = raw.get("kanban", {})
    tg_raw = raw.get("telegram", {})

    telegram = TelegramConfig(
        enabled=bool(tg_raw.get("enabled", False)),
        token=str(tg_raw.get("token", "")).strip(),
        chat_id=str(tg_raw.get("chat_id", "")).strip(),
    )
    # Fail loudly at startup rather than silently never delivering.
    if telegram.enabled and not telegram.token:
        raise ConfigError("[telegram] enabled but token is empty")
    if telegram.enabled and not telegram.chat_id:
        raise ConfigError("[telegram] enabled but chat_id is empty")

    digest_raw = raw.get("digest", {})
    weekday = int(digest_raw.get("weekday", 6))
    hour = int(digest_raw.get("hour", 19))
    if not 0 <= weekday <= 6:
        raise ConfigError(f"[digest] weekday must be 0-6, got {weekday}")
    if not 0 <= hour <= 23:
        raise ConfigError(f"[digest] hour must be 0-23, got {hour}")

    return Config(
        vault_path=vault_path,
        inbox_folder=_require(raw, "inbox_folder", "config"),
        audio_store=audio_store,
        port=int(_require(raw, "port", "config")),
        asr=AsrConfig(
            handy_exe=Path(_require(asr_raw, "handy_exe", "[asr]")).expanduser(),
            model=_require(asr_raw, "model", "[asr]"),
            timeout_s=int(_require(asr_raw, "timeout_s", "[asr]")),
        ),
        post_process=PostProcessConfig(
            enabled=bool(_require(pp_raw, "enabled", "[post_process]")),
            backend=backend,
            model=pp_raw.get("model", ""),
            answer_tasks=bool(pp_raw.get("answer_tasks", True)),
        ),
        kanban=KanbanConfig(
            enabled=bool(kanban_raw.get("enabled", True)),
            subfolder=str(kanban_raw.get("subfolder", "Quadros")),
        ),
        telegram=telegram,
        digest=DigestConfig(
            enabled=bool(digest_raw.get("enabled", True)),
            weekday=weekday,
            hour=hour,
        ),
    )
