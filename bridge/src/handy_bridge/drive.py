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
        try:
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
        except Exception as exc:  # httpx raises a family of transport errors
            # Never let the token reach a log line or an exception message.
            raise DriveError(f"could not reach Google Drive token endpoint: {type(exc).__name__}") from exc
        if response.status_code != 200:
            raise DriveError(_describe(response))
        payload = response.json()
        self._token = str(payload["access_token"])
        self._expires_at = time.monotonic() + int(payload.get("expires_in", 3600)) - EXPIRY_MARGIN_S
        return self._token

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.access_token()}"}

    def list_inbox(self) -> list[RemoteFile]:
        try:
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
        except Exception as exc:  # httpx raises a family of transport errors
            # Never let the token reach a log line or an exception message.
            raise DriveError(f"could not reach Google Drive files endpoint: {type(exc).__name__}") from exc
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
        try:
            response = self._request(
                "GET", f"{FILES_URL}/{file_id}", headers=self._headers(), params={"alt": "media"}
            )
        except Exception as exc:  # httpx raises a family of transport errors
            # Never let the token reach a log line or an exception message.
            raise DriveError(f"could not download from Google Drive: {type(exc).__name__}") from exc
        if response.status_code != 200:
            raise DriveError(_describe(response))
        return response.content

    def delete(self, file_id: str) -> None:
        try:
            response = self._request("DELETE", f"{FILES_URL}/{file_id}", headers=self._headers())
        except Exception as exc:  # httpx raises a family of transport errors
            # Never let the token reach a log line or an exception message.
            raise DriveError(f"could not delete from Google Drive: {type(exc).__name__}") from exc
        # 404 é sucesso para o nosso propósito: o arquivo não está mais lá.
        if response.status_code not in (200, 204, 404):
            raise DriveError(_describe(response))
