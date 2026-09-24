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
    # Which compute devices to ask, in order, falling to the next when one dies.
    # Empty means "let Handy choose", which is what it did before this existed.
    # It matters because the buffer a Vulkan backend allocates grows with the
    # recording's length: the 2GB GPU in this laptop transcribes a minute and
    # crashes on two, while the iGPU and the CPU have room for both.
    device_indexes: tuple[int, ...] = ()


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
class SummariesConfig:
    enabled: bool = True
    # "nota" regenerates a chapter summary on every recording in it, which is the
    # freshest and costs one `claude -p` call per note. "capitulo" waits until the
    # reading moves past the chapter, which is roughly one call per chapter
    # instead. The trade is freshness against cost, so it is a config choice.
    chapter_on: str = "nota"


VALID_CHAPTER_TRIGGERS = {"nota", "capitulo"}


@dataclass(frozen=True)
class TelegramConfig:
    enabled: bool = False
    token: str = ""
    chat_id: str = ""


@dataclass(frozen=True)
class DriveConfig:
    """Credenciais do Google Drive para a rota de queda.

    Vazia e desligada por padrão: quem só usa o bridge na própria rede não
    precisa criar projeto no Google Cloud para nada.
    """

    enabled: bool = False
    client_id: str = ""
    client_secret: str = ""
    refresh_token: str = ""
    folder_id: str = ""
    # 30 s porque a rota de queda já é a lenta: o gargalo é o upload do device,
    # e um intervalo curto multiplicaria chamadas de API sem encurtar nada.
    poll_s: int = 30


@dataclass(frozen=True)
class Config:
    vault_path: Path
    # Legacy and unused: notes now live in `Livros/<book>/Notas`, computed by the
    # `layout` module. Kept so an existing config.toml keeps loading.
    inbox_folder: str
    audio_store: Path
    port: int
    asr: AsrConfig
    post_process: PostProcessConfig
    # Optional sections: an older config.toml without them keeps working.
    kanban: KanbanConfig = KanbanConfig()
    telegram: TelegramConfig = TelegramConfig()
    digest: DigestConfig = DigestConfig()
    summaries: SummariesConfig = SummariesConfig()
    drive: DriveConfig = DriveConfig()



def _require(table: dict, key: str, where: str):
    if key not in table:
        raise ConfigError(f"missing '{key}' in {where}")
    return table[key]


def load_config(path: Path) -> Config:
    try:
        raw = tomllib.loads(Path(path).read_text(encoding="utf-8-sig"))
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

    summaries_raw = raw.get("summaries", {})
    chapter_on = str(summaries_raw.get("chapter_on", "nota")).strip().lower()
    if chapter_on not in VALID_CHAPTER_TRIGGERS:
        raise ConfigError(
            f"[summaries] chapter_on must be one of {sorted(VALID_CHAPTER_TRIGGERS)}, "
            f"got {chapter_on!r}"
        )

    digest_raw = raw.get("digest", {})
    weekday = int(digest_raw.get("weekday", 6))
    hour = int(digest_raw.get("hour", 19))
    if not 0 <= weekday <= 6:
        raise ConfigError(f"[digest] weekday must be 0-6, got {weekday}")
    if not 0 <= hour <= 23:
        raise ConfigError(f"[digest] hour must be 0-23, got {hour}")

    drive_raw = raw.get("drive", {})
    drive = DriveConfig(
        enabled=bool(drive_raw.get("enabled", False)),
        client_id=str(drive_raw.get("client_id", "")).strip(),
        client_secret=str(drive_raw.get("client_secret", "")).strip(),
        refresh_token=str(drive_raw.get("refresh_token", "")).strip(),
        folder_id=str(drive_raw.get("folder_id", "")).strip(),
        poll_s=int(drive_raw.get("poll_s", 30)),
    )
    # Falha alto ao subir, em vez de nunca entregar em silêncio -- o mesmo
    # critério aplicado ao [telegram].
    if drive.enabled:
        for field in ("client_id", "client_secret", "refresh_token", "folder_id"):
            if not getattr(drive, field):
                raise ConfigError(f"[drive] enabled but {field} is empty")
    if drive.poll_s < 5:
        raise ConfigError(f"[drive] poll_s must be at least 5, got {drive.poll_s}")

    return Config(
        vault_path=vault_path,
        inbox_folder=str(raw.get("inbox_folder", "Inbox")),
        audio_store=audio_store,
        port=int(_require(raw, "port", "config")),
        asr=AsrConfig(
            handy_exe=Path(_require(asr_raw, "handy_exe", "[asr]")).expanduser(),
            model=_require(asr_raw, "model", "[asr]"),
            timeout_s=int(_require(asr_raw, "timeout_s", "[asr]")),
            device_indexes=tuple(int(i) for i in asr_raw.get("device_indexes", ())),
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
        summaries=SummariesConfig(
            enabled=bool(summaries_raw.get("enabled", True)),
            chapter_on=chapter_on,
        ),
        drive=drive,
    )
