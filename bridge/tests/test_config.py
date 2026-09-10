from pathlib import Path

import pytest

from handy_bridge.config import ConfigError, load_config


def write(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "config.toml"
    p.write_text(body, encoding="utf-8")
    return p


def test_loads_all_fields(tmp_path):
    vault = tmp_path / "Reading"
    vault.mkdir()
    cfg = load_config(
        write(
            tmp_path,
            f"""
vault_path   = "{vault.as_posix()}"
inbox_folder = "Inbox"
audio_store  = "{(tmp_path / 'audio').as_posix()}"
port         = 8787

[asr]
handy_exe = "{(tmp_path / 'handy.exe').as_posix()}"
model     = "handy-computer/x/y.gguf"
timeout_s = 900

[post_process]
enabled = true
backend = "claude_cli"
model   = "claude-haiku-4-5-20251001"
""",
        )
    )
    assert cfg.vault_path == vault
    assert cfg.inbox_folder == "Inbox"
    assert cfg.port == 8787
    assert cfg.asr.model == "handy-computer/x/y.gguf"
    assert cfg.asr.timeout_s == 900
    assert cfg.post_process.backend == "claude_cli"
    assert cfg.post_process.model == "claude-haiku-4-5-20251001"


def test_creates_audio_store_if_missing(tmp_path):
    vault = tmp_path / "Reading"
    vault.mkdir()
    store = tmp_path / "nested" / "audio"
    cfg = load_config(
        write(
            tmp_path,
            f"""
vault_path   = "{vault.as_posix()}"
inbox_folder = "Inbox"
audio_store  = "{store.as_posix()}"
port         = 8787

[asr]
handy_exe = "{(tmp_path / 'handy.exe').as_posix()}"
model     = "m"
timeout_s = 900

[post_process]
enabled = false
backend = "none"
model   = ""
""",
        )
    )
    assert cfg.audio_store.is_dir()


def test_rejects_missing_vault(tmp_path):
    with pytest.raises(ConfigError, match="vault_path"):
        load_config(
            write(
                tmp_path,
                f"""
vault_path   = "{(tmp_path / 'nope').as_posix()}"
inbox_folder = "Inbox"
audio_store  = "{(tmp_path / 'audio').as_posix()}"
port         = 8787

[asr]
handy_exe = "h"
model     = "m"
timeout_s = 900

[post_process]
enabled = false
backend = "none"
model   = ""
""",
            )
        )


def test_rejects_unknown_backend(tmp_path):
    vault = tmp_path / "Reading"
    vault.mkdir()
    with pytest.raises(ConfigError, match="backend"):
        load_config(
            write(
                tmp_path,
                f"""
vault_path   = "{vault.as_posix()}"
inbox_folder = "Inbox"
audio_store  = "{(tmp_path / 'audio').as_posix()}"
port         = 8787

[asr]
handy_exe = "h"
model     = "m"
timeout_s = 900

[post_process]
enabled = true
backend = "gpt5"
model   = ""
""",
            )
        )


BASE = """
vault_path   = "{vault}"
inbox_folder = "Inbox"
audio_store  = "{store}"
port         = 8787

[asr]
handy_exe = "h"
model     = "m"
timeout_s = 900

[post_process]
enabled = true
backend = "claude_cli"
model   = "haiku"
"""


def base_cfg(tmp_path, extra: str = ""):
    vault = tmp_path / "Reading"
    vault.mkdir(exist_ok=True)
    body = BASE.format(vault=vault.as_posix(), store=(tmp_path / "audio").as_posix()) + extra
    return load_config(write(tmp_path, body))


def test_kanban_and_telegram_default_to_sane_values_when_absent(tmp_path):
    cfg = base_cfg(tmp_path)
    assert cfg.kanban.enabled is True
    assert cfg.kanban.subfolder == "Quadros"
    # Telegram fica desligado por padrao: sem token, nao ha entrega.
    assert cfg.telegram.enabled is False
    assert cfg.telegram.token == ""
    assert cfg.post_process.answer_tasks is True


def test_reads_explicit_kanban_and_telegram(tmp_path):
    cfg = base_cfg(
        tmp_path,
        """
[kanban]
enabled   = false
subfolder = "Boards"

[telegram]
enabled = true
token   = "123:ABC"
chat_id = "999"
""",
    )
    assert cfg.kanban.enabled is False
    assert cfg.kanban.subfolder == "Boards"
    assert cfg.telegram.enabled is True
    assert cfg.telegram.token == "123:ABC"
    assert cfg.telegram.chat_id == "999"


def test_telegram_enabled_without_token_is_rejected(tmp_path):
    with pytest.raises(ConfigError, match="token"):
        base_cfg(
            tmp_path,
            """
[telegram]
enabled = true
token   = ""
chat_id = "999"
""",
        )


def test_telegram_enabled_without_chat_id_is_rejected(tmp_path):
    with pytest.raises(ConfigError, match="chat_id"):
        base_cfg(
            tmp_path,
            """
[telegram]
enabled = true
token   = "123:ABC"
chat_id = ""
""",
        )


def test_drive_is_disabled_by_default(tmp_path):
    cfg = base_cfg(tmp_path)
    assert cfg.drive.enabled is False
    assert cfg.drive.poll_s == 30


def test_drive_reads_credentials_and_poll_interval(tmp_path):
    cfg = base_cfg(
        tmp_path,
        extra="""
[drive]
enabled = true
client_id = "cid"
client_secret = "csec"
refresh_token = "rtok"
folder_id = "fid"
poll_s = 15
""",
    )
    assert cfg.drive.enabled is True
    assert cfg.drive.client_id == "cid"
    assert cfg.drive.client_secret == "csec"
    assert cfg.drive.refresh_token == "rtok"
    assert cfg.drive.folder_id == "fid"
    assert cfg.drive.poll_s == 15


def test_drive_enabled_without_refresh_token_is_an_error(tmp_path):
    with pytest.raises(ConfigError, match="refresh_token"):
        base_cfg(
            tmp_path,
            extra='[drive]\nenabled = true\nclient_id = "c"\nclient_secret = "s"\nfolder_id = "f"\n',
        )


def test_drive_enabled_without_folder_id_is_an_error(tmp_path):
    with pytest.raises(ConfigError, match="folder_id"):
        base_cfg(
            tmp_path,
            extra='[drive]\nenabled = true\nclient_id = "c"\nclient_secret = "s"\nrefresh_token = "r"\n',
        )
