# Envio por Google Drive — Plano de Implementação

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** quando o bridge não é encontrado na rede, o device sobe a nota para o Google Drive e o bridge a busca de lá, de modo que device e PC possam estar em redes diferentes.

**Architecture:** a fila no cartão não muda. O flush tenta a LAN primeiro (`POST /v1/notes`, intocado) e cai para o Drive só quando nenhum endpoint de bridge foi encontrado. O Drive é uma **segunda porta de entrada** no bridge: um poller baixa o par `.wav`+`.json`, monta um `IncomingNote` e chama `worker.submit()` — a mesma função que o endpoint HTTP chama. A pipeline de transcrição, nota, kanban, telegram e resumos não é tocada.

**Tech Stack:** bridge em Python 3.13 (`httpx`, `pytest`); firmware C++20 no ESP32-S3 (`WiFiClientSecure`, `glaze` para TOML e JSON, `unity` para teste de host).

**Spec:** [docs/superpowers/specs/2026-09-10-envio-por-google-drive-design.md](../specs/2026-09-10-envio-por-google-drive-design.md)

## Global Constraints

- **Conta Drive:** `kakamoussallli@gmail.com`. Escopo OAuth **`drive.file`** e nada mais — dá acesso só aos arquivos que o app cria.
- **A tela de consentimento do Google tem que estar em `Published`.** Em `Testing` o refresh token expira em 7 dias e o sistema para sozinho.
- **Só entrega confirmada apaga áudio.** Todo resultado de transporte passa por `actionFor()` em `VoiceQueuePlan`, que é o único lugar onde se decide destruir uma gravação.
- **A queda para o Drive dispara só quando nenhum endpoint de bridge foi encontrado.** Nunca quando um endpoint foi achado e o envio falhou no meio — o bridge pode ter recebido, e subir a mesma nota pelo Drive criaria nota duplicada.
- **O device sobe o `.wav` primeiro e o `.json` por último.** O bridge só considera pronto um `.wav` que já tenha `.json`, ou que esteja sem sidecar há mais de 5 minutos.
- **Deduplicação por file id do Drive**, nunca por nome de arquivo.
- **TLS no device valida o certificado** (CA raiz do Google pinada). Isto **divirge de propósito** do `OtaUpdater.cpp`, que usa `setInsecure()`: aqui trafega um refresh token, e `setInsecure()` o entregaria a qualquer um na rede.
- **Config do device é TOML**, lido com glaze como o resto (`glz::opts{.format = glz::TOML}`). A spec diz `drive.json`; o arquivo é `/config/drive.toml` para seguir o padrão do repo — o device escreve JSON à mão, mas nunca lê JSON de config.
- **Testes rodam sem hardware, sem rede e sem gastar tokens.** HTTP é dublado injetando um callable, como `tests/test_telegram.py` já faz.
- Testes nativos: `PLATFORMIO_BUILD_DIR=<fora do OneDrive> pio test -e native_test -f <dir>`.

---

## Fase A — bridge (entrega valor sozinha)

Ao fim da Fase A o sistema já funciona colocando arquivos na pasta do Drive à mão, sem nenhuma mudança de firmware.

### Task 1: Seção `[drive]` no config

**Files:**
- Modify: `bridge/src/handy_bridge/config.py`
- Test: `bridge/tests/test_config.py`

**Interfaces:**
- Consumes: nada.
- Produces: `DriveConfig(enabled: bool, client_id: str, client_secret: str, refresh_token: str, folder_id: str, poll_s: int)` e o campo `Config.drive`.

- [ ] **Step 1: Write the failing tests**

Em `bridge/tests/test_config.py`, seguindo o helper `write_config` que o arquivo já usa para montar um `config.toml` mínimo:

```python
def test_drive_is_disabled_by_default(tmp_path):
    cfg = load_config(write_config(tmp_path))
    assert cfg.drive.enabled is False
    assert cfg.drive.poll_s == 30


def test_drive_reads_credentials_and_poll_interval(tmp_path):
    path = write_config(
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
    cfg = load_config(path)
    assert cfg.drive.enabled is True
    assert cfg.drive.client_id == "cid"
    assert cfg.drive.refresh_token == "rtok"
    assert cfg.drive.folder_id == "fid"
    assert cfg.drive.poll_s == 15


def test_drive_enabled_without_refresh_token_is_an_error(tmp_path):
    path = write_config(
        tmp_path,
        extra='[drive]\nenabled = true\nclient_id = "c"\nclient_secret = "s"\nfolder_id = "f"\n',
    )
    with pytest.raises(ConfigError, match="refresh_token"):
        load_config(path)


def test_drive_enabled_without_folder_id_is_an_error(tmp_path):
    path = write_config(
        tmp_path,
        extra='[drive]\nenabled = true\nclient_id = "c"\nclient_secret = "s"\nrefresh_token = "r"\n',
    )
    with pytest.raises(ConfigError, match="folder_id"):
        load_config(path)
```

Se `write_config` no arquivo ainda não aceitar um trecho extra, adicione o parâmetro `extra: str = ""` e concatene ao final do TOML.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd bridge && uv run pytest tests/test_config.py -k drive -v`
Expected: FAIL com `AttributeError: 'Config' object has no attribute 'drive'`.

- [ ] **Step 3: Write minimal implementation**

Em `config.py`, ao lado dos outros dataclasses:

```python
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
```

Adicione `drive: DriveConfig = DriveConfig()` ao dataclass `Config` (depois dos campos existentes, com default, para não quebrar os testes que constroem `Config` à mão como `tests/test_server.py:make_cfg`).

Em `load_config`, antes do `return`:

```python
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
```

E passe `drive=drive` no `return Config(...)`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd bridge && uv run pytest tests/test_config.py -v`
Expected: PASS, incluindo os testes antigos.

- [ ] **Step 5: Commit**

```bash
git add bridge/src/handy_bridge/config.py bridge/tests/test_config.py
git commit -m "feat(bridge): ler as credenciais do Drive do config"
```

---

### Task 2: Seleção do que está pronto na pasta (lógica pura)

**Files:**
- Create: `bridge/src/handy_bridge/drive_inbox.py`
- Test: `bridge/tests/test_drive_inbox.py`

**Interfaces:**
- Consumes: nada (função pura, sem rede).
- Produces: `RemoteFile(id: str, name: str, created_at: datetime)`, `ReadyNote(note_id: str, wav: RemoteFile, sidecar: RemoteFile | None)` e `plan_inbox(files: list[RemoteFile], processed_ids: set[str], now: datetime, grace_s: int = 300) -> list[ReadyNote]`.

Por que existe separado: é a mesma decisão que `planFrom()` toma no cartão, e mantê-la longe da rede é o que permite testar as regras de pareamento e de prazo sem tocar no Google. Mesmo desenho, mesma razão.

- [ ] **Step 1: Write the failing tests**

```python
from datetime import datetime, timedelta, timezone

from handy_bridge.drive_inbox import RemoteFile, plan_inbox

NOW = datetime(2026, 9, 10, 12, 0, tzinfo=timezone.utc)


def at(minutes_ago: int) -> datetime:
    return NOW - timedelta(minutes=minutes_ago)


def test_a_pair_is_ready():
    files = [
        RemoteFile("w1", "20260910-120000.wav", at(1)),
        RemoteFile("s1", "20260910-120000.json", at(1)),
    ]
    ready = plan_inbox(files, processed_ids=set(), now=NOW)
    assert len(ready) == 1
    assert ready[0].note_id == "20260910-120000"
    assert ready[0].wav.id == "w1"
    assert ready[0].sidecar.id == "s1"


def test_a_wav_without_its_sidecar_waits_for_the_grace_period():
    files = [RemoteFile("w1", "20260910-120000.wav", at(1))]
    assert plan_inbox(files, processed_ids=set(), now=NOW) == []


def test_a_wav_whose_sidecar_never_came_is_ready_after_the_grace_period():
    # Sidecar perdido não custa a nota: ela sobe sem âncora.
    files = [RemoteFile("w1", "20260910-114000.wav", at(20))]
    ready = plan_inbox(files, processed_ids=set(), now=NOW)
    assert len(ready) == 1
    assert ready[0].sidecar is None


def test_an_already_processed_wav_is_not_offered_again():
    files = [
        RemoteFile("w1", "20260910-120000.wav", at(10)),
        RemoteFile("s1", "20260910-120000.json", at(10)),
    ]
    assert plan_inbox(files, processed_ids={"w1"}, now=NOW) == []


def test_a_sidecar_without_its_recording_is_ignored():
    files = [RemoteFile("s1", "20260910-120000.json", at(10))]
    assert plan_inbox(files, processed_ids=set(), now=NOW) == []


def test_the_oldest_recording_is_offered_first():
    files = [
        RemoteFile("w2", "20260910-120000.wav", at(1)),
        RemoteFile("s2", "20260910-120000.json", at(1)),
        RemoteFile("w1", "20260910-110000.wav", at(60)),
        RemoteFile("s1", "20260910-110000.json", at(60)),
    ]
    ready = plan_inbox(files, processed_ids=set(), now=NOW)
    assert [r.note_id for r in ready] == ["20260910-110000", "20260910-120000"]


def test_files_that_are_neither_wav_nor_sidecar_are_ignored():
    files = [RemoteFile("x", "leia-me.txt", at(10))]
    assert plan_inbox(files, processed_ids=set(), now=NOW) == []
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd bridge && uv run pytest tests/test_drive_inbox.py -v`
Expected: FAIL com `ModuleNotFoundError: No module named 'handy_bridge.drive_inbox'`.

- [ ] **Step 3: Write minimal implementation**

```python
"""Decidir o que na pasta do Drive já está pronto para virar nota.

Sem rede de propósito: são as regras de pareamento e de prazo, e elas ficam
sob teste no host pela mesma razão que planFrom() fica, no firmware.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

WAV_SUFFIX = ".wav"
SIDECAR_SUFFIX = ".json"
# O device sobe o .wav e depois o .json, então um .wav sozinho pode ser um
# upload em curso. Cinco minutos passam folgado do pior upload plausível de uma
# nota de dez minutos, e depois disso o sidecar não vem mais.
DEFAULT_GRACE_S = 300


@dataclass(frozen=True)
class RemoteFile:
    id: str
    name: str
    created_at: datetime


@dataclass(frozen=True)
class ReadyNote:
    note_id: str
    wav: RemoteFile
    sidecar: RemoteFile | None


def plan_inbox(
    files: list[RemoteFile],
    processed_ids: set[str],
    now: datetime,
    grace_s: int = DEFAULT_GRACE_S,
) -> list[ReadyNote]:
    """As notas prontas, mais antiga primeiro."""
    sidecars = {
        f.name[: -len(SIDECAR_SUFFIX)]: f
        for f in files
        if f.name.lower().endswith(SIDECAR_SUFFIX)
    }
    grace = timedelta(seconds=grace_s)

    ready: list[ReadyNote] = []
    for wav in files:
        if not wav.name.lower().endswith(WAV_SUFFIX):
            continue
        if wav.id in processed_ids:
            continue
        note_id = wav.name[: -len(WAV_SUFFIX)]
        sidecar = sidecars.get(note_id)
        if sidecar is None and now - wav.created_at < grace:
            continue  # Pode ser um upload em curso.
        ready.append(ReadyNote(note_id=note_id, wav=wav, sidecar=sidecar))

    ready.sort(key=lambda r: r.wav.created_at)
    return ready
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd bridge && uv run pytest tests/test_drive_inbox.py -v`
Expected: PASS, 7 testes.

- [ ] **Step 5: Commit**

```bash
git add bridge/src/handy_bridge/drive_inbox.py bridge/tests/test_drive_inbox.py
git commit -m "feat(bridge): decidir o que na pasta do Drive já está pronto"
```

---

### Task 3: Cliente do Drive

**Files:**
- Create: `bridge/src/handy_bridge/drive.py`
- Test: `bridge/tests/test_drive.py`

**Interfaces:**
- Consumes: `DriveConfig` (Task 1), `RemoteFile` (Task 2).
- Produces: `DriveError`, e `Drive(cfg: DriveConfig, requester: Callable | None = None)` com os métodos `access_token() -> str`, `list_inbox() -> list[RemoteFile]`, `download(file_id: str) -> bytes`, `delete(file_id: str) -> None`.

`requester(method: str, url: str, **kwargs) -> Response` é injetado nos testes, como `TelegramSender(poster=...)` já faz. Em produção o default usa `httpx.request`.

- [ ] **Step 1: Write the failing tests**

```python
from datetime import timezone

import pytest

from handy_bridge.config import DriveConfig
from handy_bridge.drive import Drive, DriveError

CFG = DriveConfig(
    enabled=True,
    client_id="cid",
    client_secret="csec",
    refresh_token="rtok",
    folder_id="fid",
)


class FakeResponse:
    def __init__(self, status: int = 200, payload: dict | None = None, content: bytes = b""):
        self.status_code = status
        self._payload = payload if payload is not None else {}
        self.content = content
        self.text = ""

    def json(self) -> dict:
        return self._payload


def test_exchanges_the_refresh_token_for_an_access_token():
    seen = {}

    def requester(method, url, **kw):
        seen["method"], seen["url"], seen["data"] = method, url, kw.get("data")
        return FakeResponse(200, {"access_token": "at-1", "expires_in": 3599})

    assert Drive(CFG, requester=requester).access_token() == "at-1"
    assert seen["method"] == "POST"
    assert seen["url"] == "https://oauth2.googleapis.com/token"
    assert seen["data"]["grant_type"] == "refresh_token"
    assert seen["data"]["refresh_token"] == "rtok"


def test_reuses_the_access_token_instead_of_refreshing_every_call():
    calls = []

    def requester(method, url, **kw):
        calls.append(url)
        return FakeResponse(200, {"access_token": "at-1", "expires_in": 3599})

    drive = Drive(CFG, requester=requester)
    drive.access_token()
    drive.access_token()
    assert len(calls) == 1


def test_a_rejected_refresh_token_says_so():
    def requester(method, url, **kw):
        return FakeResponse(400, {"error": "invalid_grant"})

    with pytest.raises(DriveError, match="invalid_grant"):
        Drive(CFG, requester=requester).access_token()


def test_lists_only_the_configured_folder():
    seen = {}

    def requester(method, url, **kw):
        if url.endswith("/token"):
            return FakeResponse(200, {"access_token": "at", "expires_in": 3599})
        seen["url"], seen["params"], seen["headers"] = url, kw.get("params"), kw.get("headers")
        return FakeResponse(
            200,
            {
                "files": [
                    {
                        "id": "w1",
                        "name": "20260910-120000.wav",
                        "createdTime": "2026-09-10T12:00:00.000Z",
                    }
                ]
            },
        )

    files = Drive(CFG, requester=requester).list_inbox()
    assert len(files) == 1
    assert files[0].id == "w1"
    assert files[0].name == "20260910-120000.wav"
    assert files[0].created_at.tzinfo is not None
    assert files[0].created_at.astimezone(timezone.utc).hour == 12
    assert "'fid' in parents" in seen["params"]["q"]
    assert "trashed = false" in seen["params"]["q"]
    assert seen["headers"]["Authorization"] == "Bearer at"


def test_downloads_the_file_bytes():
    def requester(method, url, **kw):
        if url.endswith("/token"):
            return FakeResponse(200, {"access_token": "at", "expires_in": 3599})
        assert kw.get("params") == {"alt": "media"}
        return FakeResponse(200, content=b"RIFFxxxx")

    assert Drive(CFG, requester=requester).download("w1") == b"RIFFxxxx"


def test_deletes_the_file():
    seen = {}

    def requester(method, url, **kw):
        if url.endswith("/token"):
            return FakeResponse(200, {"access_token": "at", "expires_in": 3599})
        seen["method"], seen["url"] = method, url
        return FakeResponse(204)

    Drive(CFG, requester=requester).delete("w1")
    assert seen["method"] == "DELETE"
    assert seen["url"].endswith("/files/w1")


def test_an_http_error_on_listing_raises():
    def requester(method, url, **kw):
        if url.endswith("/token"):
            return FakeResponse(200, {"access_token": "at", "expires_in": 3599})
        return FakeResponse(403, {"error": {"message": "insufficientPermissions"}})

    with pytest.raises(DriveError, match="insufficientPermissions"):
        Drive(CFG, requester=requester).list_inbox()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd bridge && uv run pytest tests/test_drive.py -v`
Expected: FAIL com `ModuleNotFoundError: No module named 'handy_bridge.drive'`.

- [ ] **Step 3: Write minimal implementation**

```python
"""Falar com o Google Drive: renovar token, listar, baixar, remover.

Nada de SDK do Google: são quatro chamadas HTTP e um refresh de token, então uma
dependência custaria mais do que economiza -- a mesma razão dita em telegram.py.

O escopo é `drive.file`, então tudo aqui só alcança arquivos que o próprio app
criou. O resto do Drive do usuário é invisível para este código.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime
from typing import Callable

import httpx

from handy_bridge.config import DriveConfig
from handy_bridge.drive_inbox import RemoteFile

log = logging.getLogger(__name__)

TOKEN_URL = "https://oauth2.googleapis.com/token"
FILES_URL = "https://www.googleapis.com/drive/v3/files"
UPLOAD_URL = "https://www.googleapis.com/upload/drive/v3/files"
DEFAULT_TIMEOUT_S = 60
# Renova um pouco antes de expirar, para nenhuma chamada sair com token vencido
# por causa de latência de rede.
EXPIRY_MARGIN_S = 60


class DriveError(Exception):
    """O Drive recusou uma chamada."""


def _default_requester(method: str, url: str, **kwargs):
    return httpx.request(method, url, timeout=DEFAULT_TIMEOUT_S, **kwargs)


def _describe(response) -> str:
    """A mensagem de erro do Google, que vem em dois formatos diferentes."""
    try:
        payload = response.json()
    except Exception:
        return f"HTTP {response.status_code}"
    if isinstance(payload.get("error"), dict):
        return str(payload["error"].get("message", payload["error"]))
    if payload.get("error"):
        return f"{payload['error']}: {payload.get('error_description', '')}".strip(": ")
    return f"HTTP {response.status_code}"


class Drive:
    def __init__(self, cfg: DriveConfig, requester: Callable | None = None):
        self._cfg = cfg
        self._request = requester or _default_requester
        self._token = ""
        self._expires_at = 0.0

    def access_token(self) -> str:
        if self._token and time.monotonic() < self._expires_at:
            return self._token
        response = self._request(
            "POST",
            TOKEN_URL,
            data={
                "client_id": self._cfg.client_id,
                "client_secret": self._cfg.client_secret,
                "refresh_token": self._cfg.refresh_token,
                "grant_type": "refresh_token",
            },
        )
        if response.status_code != 200:
            raise DriveError(_describe(response))
        payload = response.json()
        self._token = str(payload["access_token"])
        self._expires_at = time.monotonic() + int(payload.get("expires_in", 3600)) - EXPIRY_MARGIN_S
        return self._token

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.access_token()}"}

    def list_inbox(self) -> list[RemoteFile]:
        response = self._request(
            "GET",
            FILES_URL,
            headers=self._headers(),
            params={
                "q": f"'{self._cfg.folder_id}' in parents and trashed = false",
                "fields": "files(id,name,createdTime)",
                "pageSize": 200,
                "orderBy": "createdTime",
            },
        )
        if response.status_code != 200:
            raise DriveError(_describe(response))
        return [
            RemoteFile(
                id=str(item["id"]),
                name=str(item["name"]),
                created_at=datetime.fromisoformat(
                    str(item["createdTime"]).replace("Z", "+00:00")
                ),
            )
            for item in response.json().get("files", [])
        ]

    def download(self, file_id: str) -> bytes:
        response = self._request(
            "GET", f"{FILES_URL}/{file_id}", headers=self._headers(), params={"alt": "media"}
        )
        if response.status_code != 200:
            raise DriveError(_describe(response))
        return response.content

    def delete(self, file_id: str) -> None:
        response = self._request("DELETE", f"{FILES_URL}/{file_id}", headers=self._headers())
        # 404 é sucesso para o nosso propósito: o arquivo não está mais lá.
        if response.status_code not in (200, 204, 404):
            raise DriveError(_describe(response))
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd bridge && uv run pytest tests/test_drive.py -v`
Expected: PASS, 7 testes.

- [ ] **Step 5: Commit**

```bash
git add bridge/src/handy_bridge/drive.py bridge/tests/test_drive.py
git commit -m "feat(bridge): cliente do Drive para listar, baixar e remover"
```

---

### Task 4: O poller que entrega ao worker

**Files:**
- Create: `bridge/src/handy_bridge/drive_poller.py`
- Test: `bridge/tests/test_drive_poller.py`

**Interfaces:**
- Consumes: `Drive` (Task 3), `plan_inbox`/`ReadyNote` (Task 2), `Config` (Task 1), `IncomingNote` de `handy_bridge.pipeline`.
- Produces: `ProcessedIds(path: Path)` com `snapshot() -> set[str]` e `add(file_id) -> None`; `DrivePoller(cfg, drive, submit, state, now=None)` com `poll_once() -> int`, `start()`, `stop()`.

`poll_once()` é público de propósito: é o que os testes chamam, sem thread nenhuma envolvida.

- [ ] **Step 1: Write the failing tests**

```python
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from handy_bridge.config import AsrConfig, Config, DriveConfig, PostProcessConfig
from handy_bridge.drive_inbox import RemoteFile
from handy_bridge.drive_poller import DrivePoller, ProcessedIds

NOW = datetime(2026, 9, 10, 12, 0, tzinfo=timezone.utc)


def make_cfg(tmp_path) -> Config:
    vault = tmp_path / "Reading"
    vault.mkdir(exist_ok=True)
    return Config(
        vault_path=vault,
        inbox_folder="Inbox",
        audio_store=tmp_path / "audio",
        port=8787,
        asr=AsrConfig(handy_exe=tmp_path / "handy.exe", model="m.gguf", timeout_s=900),
        post_process=PostProcessConfig(enabled=False, backend="none", model=""),
        drive=DriveConfig(enabled=True, client_id="c", client_secret="s",
                          refresh_token="r", folder_id="f"),
    )


class FakeDrive:
    def __init__(self, files, blobs):
        self.files = files
        self.blobs = blobs
        self.deleted: list[str] = []
        self.delete_fails = False

    def list_inbox(self):
        return list(self.files)

    def download(self, file_id):
        return self.blobs[file_id]

    def delete(self, file_id):
        if self.delete_fails:
            raise RuntimeError("delete falhou")
        self.deleted.append(file_id)


def pair(created_at=NOW - timedelta(minutes=1)):
    return [
        RemoteFile("w1", "20260910-120000.wav", created_at),
        RemoteFile("s1", "20260910-120000.json", created_at),
    ]


def blobs(meta=None):
    payload = {"clock_synced": True, "recorded_at": "2026-09-10T12:00:00"} if meta is None else meta
    return {"w1": b"RIFFxxxx", "s1": json.dumps(payload).encode("utf-8")}


def test_hands_a_ready_note_to_the_worker(tmp_path):
    cfg = make_cfg(tmp_path)
    submitted = []
    drive = FakeDrive(pair(), blobs())
    poller = DrivePoller(cfg, drive, submitted.append, ProcessedIds(tmp_path / "seen.json"),
                         now=lambda: NOW)

    assert poller.poll_once() == 1
    assert len(submitted) == 1
    assert submitted[0].note_id == "20260910-120000"
    assert submitted[0].meta["clock_synced"] is True


def test_writes_the_audio_where_the_http_route_writes_it(tmp_path):
    cfg = make_cfg(tmp_path)
    drive = FakeDrive(pair(), blobs())
    poller = DrivePoller(cfg, drive, lambda n: None, ProcessedIds(tmp_path / "seen.json"),
                         now=lambda: NOW)
    poller.poll_once()
    assert (cfg.audio_store / "20260910-120000.wav").read_bytes() == b"RIFFxxxx"


def test_removes_both_files_from_the_drive_after_handing_over(tmp_path):
    cfg = make_cfg(tmp_path)
    drive = FakeDrive(pair(), blobs())
    poller = DrivePoller(cfg, drive, lambda n: None, ProcessedIds(tmp_path / "seen.json"),
                         now=lambda: NOW)
    poller.poll_once()
    assert sorted(drive.deleted) == ["s1", "w1"]


def test_a_failed_delete_does_not_produce_a_second_note(tmp_path):
    # O arquivo continua no Drive, mas o id já está marcado como processado.
    cfg = make_cfg(tmp_path)
    submitted = []
    drive = FakeDrive(pair(), blobs())
    drive.delete_fails = True
    state = ProcessedIds(tmp_path / "seen.json")
    poller = DrivePoller(cfg, drive, submitted.append, state, now=lambda: NOW)

    assert poller.poll_once() == 1
    assert poller.poll_once() == 0
    assert len(submitted) == 1


def test_a_note_already_processed_in_an_earlier_run_is_skipped(tmp_path):
    cfg = make_cfg(tmp_path)
    state_path = tmp_path / "seen.json"
    ProcessedIds(state_path).add("w1")

    submitted = []
    poller = DrivePoller(cfg, FakeDrive(pair(), blobs()), submitted.append,
                         ProcessedIds(state_path), now=lambda: NOW)
    assert poller.poll_once() == 0
    assert submitted == []


def test_an_unreadable_sidecar_does_not_cost_the_note(tmp_path):
    cfg = make_cfg(tmp_path)
    submitted = []
    bad = {"w1": b"RIFFxxxx", "s1": b"{nao e json"}
    poller = DrivePoller(cfg, FakeDrive(pair(), bad), submitted.append,
                         ProcessedIds(tmp_path / "seen.json"), now=lambda: NOW)

    assert poller.poll_once() == 1
    assert submitted[0].meta == {}


def test_nothing_ready_means_nothing_submitted(tmp_path):
    cfg = make_cfg(tmp_path)
    submitted = []
    lone_wav = [RemoteFile("w1", "20260910-120000.wav", NOW)]
    poller = DrivePoller(cfg, FakeDrive(lone_wav, {"w1": b"x"}), submitted.append,
                         ProcessedIds(tmp_path / "seen.json"), now=lambda: NOW)
    assert poller.poll_once() == 0
    assert submitted == []
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd bridge && uv run pytest tests/test_drive_poller.py -v`
Expected: FAIL com `ModuleNotFoundError: No module named 'handy_bridge.drive_poller'`.

- [ ] **Step 3: Write minimal implementation**

```python
"""A segunda porta de entrada: notas que chegaram pelo Drive.

Termina exatamente onde o POST /v1/notes termina -- montando um IncomingNote e
chamando submit(). Tudo depois disso é a pipeline que já existia, e é por isso
que uma nota que veio do Drive produz a mesma nota que uma que veio da LAN.
"""

from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from handy_bridge.config import Config
from handy_bridge.drive_inbox import plan_inbox
from handy_bridge.pipeline import IncomingNote

log = logging.getLogger(__name__)


class ProcessedIds:
    """File ids já entregues ao worker.

    Existe porque remover do Drive pode falhar depois de a nota já ter sido
    processada, e notas são fonte: escritas uma vez, nunca reescritas. Sem esta
    lista, um delete falho viraria uma segunda nota no vault a cada poll.
    """

    def __init__(self, path: Path):
        self._path = path
        self._ids: set[str] = set()
        if path.exists():
            try:
                self._ids = set(json.loads(path.read_text(encoding="utf-8")))
            except (ValueError, OSError):
                log.warning("estado de ids do Drive ilegível em %s; começando vazio", path)

    def snapshot(self) -> set[str]:
        return set(self._ids)

    def add(self, file_id: str) -> None:
        self._ids.add(file_id)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        # Escrita atômica: o mesmo cuidado das notas, porque um arquivo de
        # estado truncado faria o bridge reprocessar tudo.
        temp = self._path.with_suffix(".tmp")
        temp.write_text(json.dumps(sorted(self._ids)), encoding="utf-8")
        temp.replace(self._path)


class DrivePoller:
    def __init__(
        self,
        cfg: Config,
        drive,
        submit: Callable[[IncomingNote], None],
        state: ProcessedIds,
        now: Callable[[], datetime] | None = None,
    ):
        self._cfg = cfg
        self._drive = drive
        self._submit = submit
        self._state = state
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def poll_once(self) -> int:
        """Entrega ao worker tudo que está pronto. Devolve quantas notas foram."""
        ready = plan_inbox(self._drive.list_inbox(), self._state.snapshot(), self._now())
        handed = 0
        for note in ready:
            meta = {}
            if note.sidecar is not None:
                try:
                    meta = json.loads(self._drive.download(note.sidecar.id).decode("utf-8"))
                except (ValueError, UnicodeDecodeError):
                    # Sidecar ilegível não custa a nota: ela entra sem âncora,
                    # como uma gravação sem sidecar no cartão.
                    log.warning("sidecar de %s ilegível; nota entra sem âncora", note.note_id)

            self._cfg.audio_store.mkdir(parents=True, exist_ok=True)
            target = self._cfg.audio_store / f"{note.note_id}.wav"
            target.write_bytes(self._drive.download(note.wav.id))

            # Marcado antes de entregar: se o processo morrer entre as duas
            # coisas, a nota é perdida uma vez. Marcar depois arriscaria
            # reprocessar e escrever a nota duas vezes, que é pior porque nada
            # no vault reconciliaria as duas.
            self._state.add(note.wav.id)
            self._submit(IncomingNote(note_id=note.note_id, wav_path=target, meta=meta))
            handed += 1
            log.info("nota %s recebida pelo Drive", note.note_id)

            for remote_id in filter(None, (note.wav.id, note.sidecar.id if note.sidecar else None)):
                try:
                    self._drive.delete(remote_id)
                except Exception as exc:  # noqa: BLE001 - remover é melhor-esforço
                    log.warning("não removi %s do Drive: %s", remote_id, exc)
        return handed

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="drive-poller", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.poll_once()
            except Exception as exc:  # noqa: BLE001 - um erro não mata o poller
                log.warning("poll do Drive falhou: %s", exc)
            self._stop.wait(self._cfg.drive.poll_s)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd bridge && uv run pytest tests/test_drive_poller.py -v`
Expected: PASS, 7 testes.

- [ ] **Step 5: Commit**

```bash
git add bridge/src/handy_bridge/drive_poller.py bridge/tests/test_drive_poller.py
git commit -m "feat(bridge): buscar no Drive as notas que não chegaram pela rede"
```

---

### Task 5: Nota do Drive percorre a pipeline inteira

**Files:**
- Test: `bridge/tests/test_drive_end_to_end.py`

**Interfaces:**
- Consumes: `DrivePoller` (Task 4), `NoteWorker` de `handy_bridge.worker`.
- Produces: nada. É a prova de que as duas portas de entrada convergem.

Por que é uma task própria: os testes anteriores provam que o poller chama `submit`. Este prova o que interessa ao usuário — que sai uma nota no vault, igual à que o `POST /v1/notes` produziria. Um revisor pode aprovar o poller e rejeitar isso.

- [ ] **Step 1: Write the failing test**

```python
import json
import struct
from datetime import datetime, timedelta, timezone

from handy_bridge.drive_inbox import RemoteFile
from handy_bridge.drive_poller import DrivePoller, ProcessedIds
from handy_bridge.transcriber import Transcription
from handy_bridge.worker import NoteWorker

from tests.test_drive_poller import FakeDrive, make_cfg

NOW = datetime(2026, 9, 10, 12, 0, tzinfo=timezone.utc)


def wav_bytes(seconds: int = 1) -> bytes:
    data = b"\x00\x01" * (16000 * seconds)
    return (
        b"RIFF"
        + struct.pack("<I", 36 + len(data))
        + b"WAVE"
        + b"fmt "
        + struct.pack("<IHHIIHH", 16, 1, 1, 16000, 32000, 2, 16)
        + b"data"
        + struct.pack("<I", len(data))
        + data
    )


def test_a_note_that_arrived_by_drive_lands_in_the_vault(tmp_path):
    cfg = make_cfg(tmp_path)
    # A transcrição é injetada como em tests/test_worker.py: sem Handy, sem áudio
    # real, sem tokens.
    worker = NoteWorker(
        cfg,
        processor=None,
        transcribe_fn=lambda wav, c: Transcription(
            "Pendencia pesquisar a caverna de Chauvet", 2.0, 1.3, "m.gguf"
        ),
    )
    worker.start()

    created = NOW - timedelta(minutes=1)
    files = [
        RemoteFile("w1", "20260910-120000.wav", created),
        RemoteFile("s1", "20260910-120000.json", created),
    ]
    drive = FakeDrive(
        files,
        {
            "w1": wav_bytes(2),
            "s1": json.dumps(
                {"clock_synced": True, "recorded_at": "2026-09-10T12:00:00"}
            ).encode("utf-8"),
        },
    )
    DrivePoller(
        cfg, drive, worker.submit, ProcessedIds(tmp_path / "seen.json"), now=lambda: NOW
    ).poll_once()

    worker.stop(timeout=10)

    # Sem `book` no sidecar, a nota é solta e cai na pasta geral -- o mesmo
    # destino que test_worker.py verifica para a rota HTTP.
    notes = list((cfg.vault_path / "Geral").rglob("*.md"))
    assert len(notes) == 1
    assert "chauvet" in notes[0].read_text(encoding="utf-8").lower()
```

`make_cfg` e `FakeDrive` vêm do arquivo de teste da Task 4, importados de verdade — não reescreva os dois.

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd bridge && uv run pytest tests/test_drive_end_to_end.py -v`
Expected: FAIL. Se falhar por import, resolva os imports de `tests.test_worker` antes de seguir — o teste tem de falhar por comportamento, não por `NameError`.

- [ ] **Step 3: Nenhuma implementação nova**

Se o teste não passar aqui, é bug de integração real entre Task 4 e o worker. Conserte a Task 4, não o teste.

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd bridge && uv run pytest -q`
Expected: toda a suíte passa.

- [ ] **Step 5: Commit**

```bash
git add bridge/tests/test_drive_end_to_end.py
git commit -m "test(bridge): nota vinda do Drive produz a mesma nota da rota HTTP"
```

---

### Task 6: Consentimento único no PC

**Files:**
- Create: `bridge/src/handy_bridge/drive_auth.py`
- Test: `bridge/tests/test_drive_auth.py`

**Interfaces:**
- Consumes: `DriveConfig` (Task 1).
- Produces: `build_consent_url(client_id: str, redirect_uri: str) -> str`, `exchange_code(client_id, client_secret, code, redirect_uri, requester) -> str` (devolve o refresh token), `device_toml(cfg: DriveConfig) -> str`, e `main(argv)` como `python -m handy_bridge.drive_auth`.

O fluxo é o de app instalado com loopback: sobe um servidor local em `127.0.0.1`, abre o navegador, o Google redireciona com `?code=`, troca o code por tokens. Aqui só as partes puras são testadas; o socket e o navegador ficam no `main`.

- [ ] **Step 1: Write the failing tests**

```python
from urllib.parse import parse_qs, urlparse

import pytest

from handy_bridge.config import DriveConfig
from handy_bridge.drive_auth import build_consent_url, device_toml, exchange_code


class FakeResponse:
    def __init__(self, status=200, payload=None):
        self.status_code = status
        self._payload = payload or {}

    def json(self):
        return self._payload


def test_the_consent_url_asks_only_for_drive_file_and_offline_access():
    url = build_consent_url("cid", "http://127.0.0.1:9004/")
    query = parse_qs(urlparse(url).query)
    assert query["scope"] == ["https://www.googleapis.com/auth/drive.file"]
    # Sem access_type=offline não vem refresh token, e sem refresh token o
    # device pararia de funcionar em uma hora.
    assert query["access_type"] == ["offline"]
    assert query["client_id"] == ["cid"]


def test_exchanging_the_code_returns_the_refresh_token():
    def requester(method, url, **kw):
        assert kw["data"]["grant_type"] == "authorization_code"
        assert kw["data"]["code"] == "the-code"
        return FakeResponse(200, {"refresh_token": "rtok", "access_token": "at"})

    token = exchange_code("cid", "csec", "the-code", "http://127.0.0.1:9004/", requester)
    assert token == "rtok"


def test_a_response_without_a_refresh_token_is_an_error():
    # Acontece quando a conta já autorizou o app antes: o Google devolve só
    # access_token, e seguir em frente escreveria um config quebrado.
    def requester(method, url, **kw):
        return FakeResponse(200, {"access_token": "at"})

    with pytest.raises(RuntimeError, match="refresh"):
        exchange_code("cid", "csec", "c", "http://127.0.0.1:9004/", requester)


def test_the_device_config_is_toml_the_firmware_can_read():
    cfg = DriveConfig(enabled=True, client_id="cid", client_secret="csec",
                      refresh_token="rtok", folder_id="fid")
    text = device_toml(cfg)
    assert 'client_id = "cid"' in text
    assert 'refresh_token = "rtok"' in text
    assert 'folder_id = "fid"' in text
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd bridge && uv run pytest tests/test_drive_auth.py -v`
Expected: FAIL com `ModuleNotFoundError: No module named 'handy_bridge.drive_auth'`.

- [ ] **Step 3: Write minimal implementation**

```python
"""Consentimento único: rende o refresh token e o arquivo que vai no cartão.

Fluxo de app instalado com loopback, não device flow: o navegador do PC já está
aqui, e uma tela de código no device seria trabalho de firmware para resolver um
problema que não existe.
"""

from __future__ import annotations

import argparse
import http.server
import threading
import urllib.parse
import webbrowser
from pathlib import Path

import httpx

from handy_bridge.config import DriveConfig, load_config

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
SCOPE = "https://www.googleapis.com/auth/drive.file"
REDIRECT_PORT = 9004


def build_consent_url(client_id: str, redirect_uri: str) -> str:
    query = urllib.parse.urlencode(
        {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": SCOPE,
            "access_type": "offline",
            # Força a tela de consentimento mesmo se a conta já autorizou, que é
            # o que garante um refresh token novo em vez de só access_token.
            "prompt": "consent",
        }
    )
    return f"{AUTH_URL}?{query}"


def exchange_code(client_id: str, client_secret: str, code: str, redirect_uri: str,
                  requester=None) -> str:
    request = requester or (lambda m, u, **kw: httpx.request(m, u, timeout=60, **kw))
    response = request(
        "POST",
        TOKEN_URL,
        data={
            "client_id": client_id,
            "client_secret": client_secret,
            "code": code,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
        },
    )
    payload = response.json()
    if response.status_code != 200:
        raise RuntimeError(f"Google recusou a troca do code: {payload}")
    token = payload.get("refresh_token")
    if not token:
        raise RuntimeError(
            "o Google não devolveu refresh token; revogue o acesso do app na conta "
            "e rode de novo"
        )
    return str(token)


def device_toml(cfg: DriveConfig) -> str:
    """O /config/drive.toml que vai para o cartão, via transferência USB."""
    return (
        "# Gerado por handy_bridge.drive_auth. Copie para /config/drive.toml no cartão.\n"
        f'client_id = "{cfg.client_id}"\n'
        f'client_secret = "{cfg.client_secret}"\n'
        f'refresh_token = "{cfg.refresh_token}"\n'
        f'folder_id = "{cfg.folder_id}"\n'
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="handy-bridge drive-auth")
    parser.add_argument("--config", type=Path, default=Path("config.toml"))
    parser.add_argument("--out", type=Path, default=Path("drive.toml"))
    args = parser.parse_args(argv)

    cfg = load_config(args.config).drive
    if not cfg.client_id or not cfg.client_secret:
        print("preencha client_id e client_secret em [drive] antes de autorizar")
        return 2

    redirect_uri = f"http://127.0.0.1:{REDIRECT_PORT}/"
    captured: dict[str, str] = {}
    done = threading.Event()

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 - assinatura da stdlib
            query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            captured.update({k: v[0] for k, v in query.items()})
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write("Autorizado. Pode fechar esta aba.".encode("utf-8"))
            done.set()

        def log_message(self, *_args):
            pass

    server = http.server.HTTPServer(("127.0.0.1", REDIRECT_PORT), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()

    url = build_consent_url(cfg.client_id, redirect_uri)
    print(f"abrindo o navegador; se não abrir, acesse:\n{url}")
    webbrowser.open(url)
    done.wait(timeout=300)
    server.shutdown()

    if "code" not in captured:
        print(f"não recebi o code do Google: {captured or 'nada'}")
        return 3

    token = exchange_code(cfg.client_id, cfg.client_secret, captured["code"], redirect_uri)
    print("\ncole isto na seção [drive] do config.toml:\n")
    print(f'refresh_token = "{token}"')

    args.out.write_text(
        device_toml(DriveConfig(**{**cfg.__dict__, "refresh_token": token})), encoding="utf-8"
    )
    print(f"\ne copie {args.out} para /config/drive.toml no cartão do device")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd bridge && uv run pytest tests/test_drive_auth.py -v`
Expected: PASS, 4 testes.

- [ ] **Step 5: Commit**

```bash
git add bridge/src/handy_bridge/drive_auth.py bridge/tests/test_drive_auth.py
git commit -m "feat(bridge): comando de consentimento único do Drive"
```

---

### Task 7: Subir o poller com o serviço, e documentar

**Files:**
- Modify: `bridge/src/handy_bridge/__main__.py`
- Modify: `bridge/README.md`
- Modify: `bridge/config.example.toml`

**Interfaces:**
- Consumes: `DrivePoller`, `ProcessedIds` (Task 4), `Drive` (Task 3).
- Produces: nada.

**Esta task não ganha teste automatizado, e isso é uma escolha.** Os testes de `tests/test_main_logging.py` param no retorno antecipado por config inexistente e nunca chegam ao uvicorn — de propósito. Cobrir o startup do poller exigiria dublar uvicorn, mDNS, worker e Telegram, e o andaime seria maior e mais frágil que a fiação de seis linhas que ele testaria. A verificação é o Step 4.

- [ ] **Step 1: Write minimal implementation**

O poller precisa de `worker.submit`, então ele nasce **depois** do worker. Em `__main__.py`, imediatamente após `worker.start()`:

```python
    drive_poller = None
    if cfg.drive.enabled:
        drive_poller = DrivePoller(
            cfg,
            Drive(cfg.drive),
            worker.submit,
            ProcessedIds(cfg.audio_store / "drive-seen.json"),
        )
        drive_poller.start()
        logging.getLogger(__name__).info(
            "Drive fallback enabled, polling every %ds", cfg.drive.poll_s
        )
```

E no `finally` do `uvicorn.run`, antes de `worker.stop()`:

```python
        if drive_poller is not None:
            drive_poller.stop()
```

Importe `Drive`, `DrivePoller` e `ProcessedIds` no topo.

- [ ] **Step 2: Run the whole suite to check for regressions**

Run: `cd bridge && uv run pytest -q`
Expected: toda a suíte passa. Nada aqui é coberto por teste novo; o objetivo é provar que a fiação não quebrou o que já existia.

- [ ] **Step 3: Documentar, e corrigir a promessa de privacidade**

Em `bridge/README.md`:

1. Acrescente `[drive]` ao trecho de configuração e ao `config.example.toml`, com comentário de que fica desligado por padrão.
2. Nova seção **"Quando o bridge não está na rede"** explicando: a LAN é tentada primeiro; a queda para o Drive só ocorre quando nenhum bridge é encontrado; o escopo é `drive.file`; e **a tela de consentimento tem de estar em Published, senão o refresh token expira em 7 dias**.
3. **Corrija o texto de privacidade.** Hoje o README abre com "O áudio nunca sai da sua máquina". Isso deixa de ser verdade na rota de queda. Substitua por uma afirmação que se sustenta, por exemplo:

> A **transcrição** nunca sai da sua máquina: ela roda localmente com o Nemotron, e nenhum serviço de nuvem recebe seu áudio para transcrever. Se você habilitar a rota de queda do Drive, o WAV das notas que não acharam o bridge na rede transita e repousa na **sua** conta Google até o bridge buscá-lo — legível por você e por quem tem acesso a essa conta.

- [ ] **Step 4: Verificação manual**

Suba o bridge com `[drive]` habilitado. Esperado no log: `Drive fallback enabled, polling every 30s`. Coloque à mão um par `.wav`+`.json` na pasta do Drive e confirme que a nota aparece no vault em até 30 s, e que os dois arquivos saem da pasta. Isto é o teste de aceitação da Fase A inteira — a partir daqui o sistema já funciona sem tocar no firmware.

- [ ] **Step 5: Commit**

```bash
git add bridge/src/handy_bridge/__main__.py bridge/README.md bridge/config.example.toml
git commit -m "feat(bridge): subir o poller do Drive, e corrigir a promessa de privacidade"
```

---

## Fase B — device

Só faz sentido depois da Fase A: é ela que consome o que o device passa a subir.

### Task 8: `actionFor()` entende os resultados do Drive

**Files:**
- Modify: `src/voice/VoiceQueuePlan.h`
- Modify: `src/voice/VoiceQueuePlan.cpp`
- Test: `test/test_voice_queue/test_main.cpp`

**Interfaces:**
- Consumes: `UploadResult`, `QueueAction`, `actionFor` já existentes.
- Produces: `enum class DriveResult : uint8_t { Sent, Retry, Unauthorized, NoInternet }` e `QueueAction actionFor(DriveResult)`.

Uma sobrecarga, não uma função nova: a regra "só entrega confirmada apaga áudio" continua com um nome só.

- [ ] **Step 1: Write the failing tests**

Em `test/test_voice_queue/test_main.cpp`, junto dos testes de `actionFor` que já existem:

```cpp
    void test_a_drive_upload_that_landed_leaves_the_queue() {
        TEST_ASSERT_EQUAL(static_cast<int>(voice::QueueAction::Delete),
                          static_cast<int>(voice::actionFor(voice::DriveResult::Sent)));
    }

    void test_a_rejected_drive_token_keeps_the_recording() {
        // 401/403 do Drive fala do token, nunca da gravação. Estacionar diria
        // que a nota é ruim; ela fica na fila e sobe quando o token for
        // corrigido.
        TEST_ASSERT_EQUAL(static_cast<int>(voice::QueueAction::Keep),
                          static_cast<int>(voice::actionFor(voice::DriveResult::Unauthorized)));
    }

    void test_no_internet_keeps_the_recording() {
        TEST_ASSERT_EQUAL(static_cast<int>(voice::QueueAction::Keep),
                          static_cast<int>(voice::actionFor(voice::DriveResult::NoInternet)));
    }

    void test_a_transient_drive_failure_keeps_the_recording() {
        TEST_ASSERT_EQUAL(static_cast<int>(voice::QueueAction::Keep),
                          static_cast<int>(voice::actionFor(voice::DriveResult::Retry)));
    }
```

Registre os quatro com `RUN_TEST(...)` ao lado dos existentes.

- [ ] **Step 2: Run the tests to verify they fail**

Run (do raiz do repo):
```bash
PLATFORMIO_BUILD_DIR="$TMPDIR/pio-build" pio test -e native_test -f test_voice_queue
```
Expected: FAIL de compilação — `DriveResult` não existe.

- [ ] **Step 3: Write minimal implementation**

Em `VoiceQueuePlan.h`, depois de `UploadResult`:

```cpp
    // O resultado de uma tentativa pelo Drive. Separado de UploadResult porque
    // os erros significam coisas diferentes: um 4xx do bridge é um juízo sobre
    // a nota, e um 401 do Drive é um problema de token que não diz nada sobre
    // ela.
    enum class DriveResult : uint8_t {
        Sent,          // upload confirmado, com file id
        Retry,         // 429, 5xx, ou falha de rede
        Unauthorized,  // 401/403: token ou permissão
        NoInternet,    // não deu para falar com o Google
    };

    QueueAction actionFor(DriveResult result);
```

Em `VoiceQueuePlan.cpp`, ao lado da sobrecarga existente:

```cpp
    QueueAction actionFor(DriveResult result) {
        switch (result) {
        case DriveResult::Sent:
            return QueueAction::Delete;
        case DriveResult::Retry:
        case DriveResult::Unauthorized:
        case DriveResult::NoInternet:
            break;
        }
        // Nenhuma falha do Drive estaciona: estacionar afirma que a gravação
        // não serve, e o Drive nunca disse isso -- ele nem a olhou.
        return QueueAction::Keep;
    }
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `PLATFORMIO_BUILD_DIR="$TMPDIR/pio-build" pio test -e native_test -f test_voice_queue`
Expected: PASS, todos os testes do diretório.

- [ ] **Step 5: Commit**

```bash
git add src/voice/VoiceQueuePlan.h src/voice/VoiceQueuePlan.cpp test/test_voice_queue/test_main.cpp
git commit -m "feat(voice): traduzir resultado do Drive em ação de fila"
```

---

### Task 9: Ler `/config/drive.toml` e montar as requisições

**Files:**
- Create: `src/voice/DriveRequest.h`
- Create: `src/voice/DriveRequest.cpp`
- Create: `test/test_voice_drive_request/test_main.cpp`
- Modify: `platformio.ini` (adicionar `test_voice_drive_request` ao `test_filter`)

**Interfaces:**
- Consumes: nada (puro; sem Arduino, sem rede).
- Produces:
  - `struct DriveCredentials { std::string clientId, clientSecret, refreshToken, folderId; }`
  - `bool parseDriveConfig(std::string_view toml, DriveCredentials& out)`
  - `std::string refreshBody(const DriveCredentials&)`
  - `bool parseAccessToken(std::string_view json, std::string& out)`
  - `std::string uploadMetadata(std::string_view name, std::string_view folderId)`
  - `std::string uploadHeader(std::string_view boundary, std::string_view metadata, std::string_view contentType)`
  - `std::string uploadFooter(std::string_view boundary)`
  - `constexpr char kUploadBoundary[]`

Mesmo desenho do `VoiceUploadBody`, e pela mesma razão: montagem de corpo cabe em teste de host, socket não.

- [ ] **Step 1: Write the failing tests**

```cpp
#include <unity.h>

#include <string>

#include "voice/DriveRequest.h"

namespace {

    void test_the_config_is_read_from_toml() {
        voice::DriveCredentials creds;
        const std::string toml =
            "client_id = \"cid\"\n"
            "client_secret = \"csec\"\n"
            "refresh_token = \"rtok\"\n"
            "folder_id = \"fid\"\n";
        TEST_ASSERT_TRUE(voice::parseDriveConfig(toml, creds));
        TEST_ASSERT_EQUAL_STRING("cid", creds.clientId.c_str());
        TEST_ASSERT_EQUAL_STRING("rtok", creds.refreshToken.c_str());
        TEST_ASSERT_EQUAL_STRING("fid", creds.folderId.c_str());
    }

    void test_a_config_missing_the_refresh_token_is_refused() {
        // Metade da credencial é pior que nenhuma: renderia 401 a cada flush.
        voice::DriveCredentials creds;
        TEST_ASSERT_FALSE(voice::parseDriveConfig("client_id = \"cid\"\n", creds));
    }

    void test_garbage_config_is_refused() {
        voice::DriveCredentials creds;
        TEST_ASSERT_FALSE(voice::parseDriveConfig("isto nao e toml {{{", creds));
    }

    void test_the_refresh_body_asks_for_a_refresh_grant() {
        voice::DriveCredentials creds{"cid", "csec", "rtok", "fid"};
        const std::string body = voice::refreshBody(creds);
        TEST_ASSERT_NOT_NULL(strstr(body.c_str(), "grant_type=refresh_token"));
        TEST_ASSERT_NOT_NULL(strstr(body.c_str(), "refresh_token=rtok"));
        TEST_ASSERT_NOT_NULL(strstr(body.c_str(), "client_id=cid"));
    }

    void test_the_access_token_is_read_from_the_response() {
        std::string token;
        TEST_ASSERT_TRUE(voice::parseAccessToken(
            "{\"access_token\":\"at-1\",\"expires_in\":3599}", token));
        TEST_ASSERT_EQUAL_STRING("at-1", token.c_str());
    }

    void test_an_error_response_yields_no_token() {
        std::string token;
        TEST_ASSERT_FALSE(voice::parseAccessToken("{\"error\":\"invalid_grant\"}", token));
    }

    void test_the_upload_metadata_names_the_file_and_its_folder() {
        const std::string meta = voice::uploadMetadata("boot-00041020.wav", "fid");
        TEST_ASSERT_NOT_NULL(strstr(meta.c_str(), "\"name\":\"boot-00041020.wav\""));
        TEST_ASSERT_NOT_NULL(strstr(meta.c_str(), "\"parents\":[\"fid\"]"));
    }

    void test_the_multipart_related_body_carries_metadata_then_content() {
        const std::string head =
            voice::uploadHeader(voice::kUploadBoundary, "{\"name\":\"a.wav\"}", "audio/wav");
        TEST_ASSERT_NOT_NULL(strstr(head.c_str(), "application/json"));
        TEST_ASSERT_NOT_NULL(strstr(head.c_str(), "audio/wav"));
        // A ordem importa: o Google exige metadata antes do conteúdo.
        TEST_ASSERT_TRUE(strstr(head.c_str(), "application/json")
                         < strstr(head.c_str(), "audio/wav"));
        const std::string foot = voice::uploadFooter(voice::kUploadBoundary);
        TEST_ASSERT_NOT_NULL(strstr(foot.c_str(), "--"));
    }

} // namespace

int main(int, char**) {
    UNITY_BEGIN();
    RUN_TEST(test_the_config_is_read_from_toml);
    RUN_TEST(test_a_config_missing_the_refresh_token_is_refused);
    RUN_TEST(test_garbage_config_is_refused);
    RUN_TEST(test_the_refresh_body_asks_for_a_refresh_grant);
    RUN_TEST(test_the_access_token_is_read_from_the_response);
    RUN_TEST(test_an_error_response_yields_no_token);
    RUN_TEST(test_the_upload_metadata_names_the_file_and_its_folder);
    RUN_TEST(test_the_multipart_related_body_carries_metadata_then_content);
    return UNITY_END();
}
```

Adicione `  test_voice_drive_request` à lista `test_filter` em `platformio.ini`, junto de `test_voice_upload_body`.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `PLATFORMIO_BUILD_DIR="$TMPDIR/pio-build" pio test -e native_test -f test_voice_drive_request`
Expected: FAIL de compilação — `voice/DriveRequest.h` não existe.

- [ ] **Step 3: Write minimal implementation**

`DriveRequest.h` declara o que está no bloco Interfaces. Em `DriveRequest.cpp`:

- `parseDriveConfig` usa glaze em TOML, como o resto do firmware lê config:
  ```cpp
  const auto error = glz::read<glz::opts{.format = glz::TOML, .error_on_unknown_keys = false}>(
      out, toml);
  if (error) return false;
  return !out.clientId.empty() && !out.clientSecret.empty()
      && !out.refreshToken.empty() && !out.folderId.empty();
  ```
  Isso exige um `glz::meta` mapeando `client_id` → `clientId` e os outros três, no mesmo estilo do `SettingsModel.h`.
- `parseAccessToken` usa glaze em JSON (`glz::opts{.format = glz::JSON, .error_on_unknown_keys = false}`) numa struct com só `access_token`, e devolve `false` se vier vazio.
- `refreshBody` monta `application/x-www-form-urlencoded` concatenando os quatro campos. Os valores são tokens do Google (base64url) e não precisam de escape.
- `uploadMetadata` monta o JSON à mão, reusando `escapeJsonString` de `VoiceNoteMeta.h` para o nome.
- `uploadHeader`/`uploadFooter`/`kUploadBoundary` são o `multipart/related`, com a mesma forma do `VoiceUploadBody.cpp`: `Content-Type: application/json; charset=UTF-8` na primeira parte, o content type recebido na segunda.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `PLATFORMIO_BUILD_DIR="$TMPDIR/pio-build" pio test -e native_test -f test_voice_drive_request`
Expected: PASS, 8 testes.

- [ ] **Step 5: Commit**

```bash
git add src/voice/DriveRequest.h src/voice/DriveRequest.cpp \
        test/test_voice_drive_request/test_main.cpp platformio.ini
git commit -m "feat(voice): montar as requisições do Drive, sob teste de host"
```

---

### Task 10: O upload em si

**Files:**
- Create: `src/voice/DriveUploader.h`
- Create: `src/voice/DriveUploader.cpp`

**Interfaces:**
- Consumes: `DriveRequest` (Task 9), `DriveResult` (Task 8), `QueueEntry`.
- Produces: `std::optional<DriveCredentials> loadDriveConfig(fs::FS&)` e `DriveResult uploadToDrive(fs::FS&, const DriveCredentials&, const QueueEntry&)`.

**Esta task não tem teste unitário, e isso é deliberado.** É I/O: TLS, socket, leitura do cartão. O que era testável foi extraído para a Task 9. A verificação aqui é a compilação no CI mais um teste manual no Step 4 — dizer que está testada seria mentira.

- [ ] **Step 1: Implementar**

`DriveUploader.cpp`:
- `loadDriveConfig` lê `/config/drive.toml` (até 2 KB) e chama `parseDriveConfig`.
- `uploadToDrive`:
  1. `WiFiClientSecure client; client.setCACert(kGoogleRootCa);` — **não** `setInsecure()`. `kGoogleRootCa` é a raiz GTS R1, num `constexpr char[]` no `.cpp`, com comentário dizendo quando expira.
  2. `POST https://oauth2.googleapis.com/token` com `refreshBody`. 400/401 → `DriveResult::Unauthorized`. Falha de conexão → `NoInternet`.
  3. `parseAccessToken`; vazio → `Unauthorized`.
  4. `POST https://www.googleapis.com/upload/drive/v3/files?uploadType=multipart` com `Authorization: Bearer`, `Content-Type: multipart/related; boundary=...`, e o corpo em três pedaços — header, arquivo em blocos de 4 KB do cartão, footer — exatamente como `VoiceUploader::upload` já faz para não montar 19 MB na RAM.
  5. Repita para o sidecar, **depois** do WAV, com `application/json`. WAV enviado e sidecar falhado → `Retry`: o par incompleto espera o prazo de 5 min do bridge e não perde a nota.
  6. 200/201 → `Sent`. 401/403 → `Unauthorized`. 429/5xx → `Retry`.
- Log em `ESP_LOGI`/`ESP_LOGW` com `kTag = "voice"`, **sem o token na mensagem** — o mesmo cuidado de `fix(bridge): não deixar o token do Telegram cair no log`.

- [ ] **Step 2: Compilar para a placa**

Run: `PLATFORMIO_BUILD_DIR="$TMPDIR/pio-build" pio run -e waveshare_esp32s3_touch_lcd_349_rev2`
Expected: SUCCESS. Se o build local estiver quebrado no SCons, use o CI: `gh workflow run firmware-build.yml -R CaquiZao/rsvpnano --ref <branch> -f firmware_tag=manual-build`.

- [ ] **Step 3: Commit**

```bash
git add src/voice/DriveUploader.h src/voice/DriveUploader.cpp
git commit -m "feat(voice): subir a nota para o Drive por TLS validado"
```

- [ ] **Step 4: Verificação manual (obrigatória, não opcional)**

Com `/config/drive.toml` no cartão e o bridge **desligado**: grave uma nota. Esperado — o arquivo aparece na pasta do Drive; ao ligar o bridge, a nota entra no vault em até 30 s; a nota sai da fila do device só depois do upload confirmado.

---

### Task 11: O galho de queda, e um erro que diz a verdade

**Files:**
- Modify: `src/voice/VoiceService.cpp`
- Modify: `src/voice/VoiceService.h` (se precisar guardar as credenciais)

**Interfaces:**
- Consumes: `loadDriveConfig`, `uploadToDrive` (Task 10), `actionFor(DriveResult)` (Task 8).
- Produces: nada.

- [ ] **Step 1: Implementar a queda**

Em `flushOnce()`, hoje a ausência de endpoint faz:

```cpp
        if (!endpoint) {
            net::disconnect();
            busy_ = false;
            lastError_ = "Bridge nao esta na rede";
            return;
        }
```

Substitua por: se `loadDriveConfig` devolver credenciais, itere os `items` chamando `uploadToDrive` e traduza cada resultado com `actionFor(DriveResult)`, usando o **mesmo** `switch` de ações da rota LAN (`Keep` para, `Park` estaciona, `Delete` remove). Sem credenciais, mantenha o retorno antecipado.

A queda entra **somente** aqui — no caminho em que nenhum endpoint foi encontrado. Não a coloque no galho de `QueueAction::Keep` da rota LAN: ali o bridge foi achado e pode ter recebido a nota.

- [ ] **Step 2: As mensagens da seção 6.1 da spec**

| Situação | `lastError_` |
|---|---|
| Bridge não achado, sem `/config/drive.toml` | `"Bridge fora da rede; Drive nao configurado"` |
| Bridge não achado, Drive sem internet | `"Bridge fora da rede; sem internet"` |
| Bridge não achado, Drive recusou o token | `"Drive recusou: token"` |
| Bridge achado, envio caiu no meio | `"Envio falhou"` (inalterado) |

Sem acento, como as outras strings de UI do arquivo.

- [ ] **Step 3: Rodar os testes nativos**

Run: `PLATFORMIO_BUILD_DIR="$TMPDIR/pio-build" pio test -e native_test`
Expected: PASS. Nada aqui é coberto por teste de host — o objetivo é provar que não houve regressão.

- [ ] **Step 4: Compilar para a placa**

Run: `PLATFORMIO_BUILD_DIR="$TMPDIR/pio-build" pio run -e waveshare_esp32s3_touch_lcd_349_rev2`
Expected: SUCCESS.

- [ ] **Step 5: Commit**

```bash
git add src/voice/VoiceService.cpp src/voice/VoiceService.h
git commit -m "feat(voice): cair para o Drive quando o bridge não está na rede"
```

- [ ] **Step 6: Verificação manual de ponta a ponta**

1. PC e device em redes **diferentes**, bridge rodando: grave uma nota. Esperado: a nota aparece no vault em até 30 s.
2. Device sem internet e sem bridge: grave. Esperado: a tela diz `"Bridge fora da rede; sem internet"`, e a nota **continua na fila**.
3. Renomeie `/config/drive.toml` no cartão e repita sem bridge. Esperado: `"Bridge fora da rede; Drive nao configurado"`, nota na fila.
4. Bridge na mesma rede: grave. Esperado: entrega pela LAN, e **nada** aparece na pasta do Drive.

---

## Notas para quem executar

- **Nunca** apague uma gravação sem entrega confirmada. Todo caminho novo passa por `actionFor()`.
- O `POST /v1/notes` não muda em nenhuma task. Se você precisou mexer nele, algo saiu do plano.
- A verificação manual das Tasks 10 e 11 não é opcional: são as únicas partes sem teste automatizado, e o cenário 4 da Task 11 é o que prova que a LAN continua sendo a rota preferida.
