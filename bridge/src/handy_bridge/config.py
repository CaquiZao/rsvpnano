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


@dataclass(frozen=True)
class Config:
    vault_path: Path
    inbox_folder: str
    audio_store: Path
    port: int
    asr: AsrConfig
    post_process: PostProcessConfig

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
        ),
    )
