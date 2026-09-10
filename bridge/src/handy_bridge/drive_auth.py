"""Consentimento único: rende o refresh token e o arquivo que vai no cartão.

Fluxo de app instalado com loopback, não device flow: o navegador do PC já está
aqui, e uma tela de código no device seria trabalho de firmware para resolver um
problema que não existe.
"""

from __future__ import annotations

import argparse
import dataclasses
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
        device_toml(dataclasses.replace(cfg, refresh_token=token)), encoding="utf-8"
    )
    print(f"\ne copie {args.out} para /config/drive.toml no cartão do device")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
