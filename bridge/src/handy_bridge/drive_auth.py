"""Consentimento único: rende o refresh token e o arquivo que vai no cartão.

Fluxo de app instalado com loopback, não device flow: o navegador do PC já está
aqui, e uma tela de código no device seria trabalho de firmware para resolver um
problema que não existe.
"""

from __future__ import annotations

import argparse
import dataclasses
import http.server
import secrets
import threading
import urllib.parse
import webbrowser
from dataclasses import dataclass
from pathlib import Path

import httpx

from handy_bridge.config import DriveConfig, load_config

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
SCOPE = "https://www.googleapis.com/auth/drive.file"
REDIRECT_PORT = 9004


def build_consent_url(client_id: str, redirect_uri: str, state: str) -> str:
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
            # Volta no redirect e é comparado antes de trocar o code. Sem ele,
            # o loopback aceita o `code` de qualquer um -- ver read_redirect.
            "state": state,
        }
    )
    return f"{AUTH_URL}?{query}"


@dataclass(frozen=True)
class Redirect:
    """O que o navegador trouxe de volta no loopback."""

    code: str
    error: str
    state_ok: bool


def read_redirect(path: str, expected_state: str) -> Redirect | None:
    """O redirect do Google, ou None se a requisição não é ele.

    Duas coisas que o handler antigo não fazia. Ele encerrava a espera no
    primeiro GET de qualquer tipo, então um favicon pedido pelo navegador
    abortava o consentimento antes de o Google responder -- por isso None
    para quem não traz nem `code` nem `error`.

    E não conferia o `state`, que nem era enviado: o loopback fica 300
    segundos aceitando qualquer GET em 127.0.0.1:9004, e uma página aberta
    nessa janela podia injetar o próprio `code` e ligar este bridge ao Drive
    de outra pessoa.
    """
    query = urllib.parse.parse_qs(urllib.parse.urlparse(path).query)
    first = {key: value[0] for key, value in query.items()}
    if "code" not in first and "error" not in first:
        return None
    return Redirect(
        code=first.get("code", ""),
        error=first.get("error", ""),
        # secrets.compare_digest: a comparação é de um valor que veio de fora.
        state_ok=secrets.compare_digest(first.get("state", ""), expected_state),
    )


def exchange_code(client_id: str, client_secret: str, code: str, redirect_uri: str,
                  requester=None) -> str:
    request = requester or (lambda m, u, **kw: httpx.request(m, u, timeout=60, **kw))
    try:
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
    except Exception as exc:
        raise RuntimeError(f"não consegui alcançar Google: {type(exc).__name__}") from exc

    if response.status_code != 200:
        # Try to parse JSON for a better error message, but handle non-JSON responses
        try:
            payload = response.json()
            raise RuntimeError(f"Google recusou a troca do code (HTTP {response.status_code}): {payload}")
        except ValueError:
            # Response is not valid JSON
            raise RuntimeError(f"Google recusou a troca do code (HTTP {response.status_code})")

    payload = response.json()
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
    # Enviado no consentimento e conferido na volta: é o que amarra o redirect
    # que chega ao pedido que este processo fez.
    state = secrets.token_urlsafe(24)
    captured: dict[str, Redirect] = {}
    done = threading.Event()

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 - assinatura da stdlib
            redirect = read_redirect(self.path, state)
            if redirect is None:
                # Nem code nem error: não é o Google respondendo. Responder e
                # continuar esperando, em vez de encerrar a espera.
                self.send_response(204)
                self.end_headers()
                return
            captured["redirect"] = redirect
            answer = (
                "Autorizado. Pode fechar esta aba."
                if redirect.state_ok and redirect.code
                else "Nao reconheci esta resposta; nada foi autorizado."
            )
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(answer.encode("utf-8"))
            done.set()

        def log_message(self, *_args):
            pass

    server = http.server.HTTPServer(("127.0.0.1", REDIRECT_PORT), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()

    url = build_consent_url(cfg.client_id, redirect_uri, state)
    print(f"abrindo o navegador; se não abrir, acesse:\n{url}")
    webbrowser.open(url)
    done.wait(timeout=300)
    server.shutdown()

    redirect = captured.get("redirect")
    if redirect is None:
        print("não recebi o code do Google: nada")
        return 3
    if not redirect.state_ok:
        print(
            "o redirect não trouxe o state que eu enviei, então não é resposta "
            "ao meu pedido; nada foi autorizado. Rode de novo."
        )
        return 4
    if not redirect.code:
        print(f"o Google não autorizou: {redirect.error}")
        return 3

    token = exchange_code(cfg.client_id, cfg.client_secret, redirect.code, redirect_uri)
    print("\ncole isto na seção [drive] do config.toml:\n")
    print(f'refresh_token = "{token}"')

    args.out.write_text(
        device_toml(dataclasses.replace(cfg, refresh_token=token)), encoding="utf-8"
    )
    print(f"\ne copie {args.out} para /config/drive.toml no cartão do device")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
