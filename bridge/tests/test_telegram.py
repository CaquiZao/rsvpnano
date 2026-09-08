import pytest

from handy_bridge.telegram import TelegramError, TelegramSender, build_message


class FakeResponse:
    def __init__(self, status: int = 200, payload: dict | None = None):
        self.status_code = status
        self._payload = payload if payload is not None else {"ok": True}

    def json(self) -> dict:
        return self._payload


def test_sends_to_the_configured_chat():
    seen = {}

    def poster(url, data, timeout):
        seen["url"], seen["data"], seen["timeout"] = url, data, timeout
        return FakeResponse()

    TelegramSender("TOKEN123", "456", poster=poster).send("olá")
    assert "TOKEN123" in seen["url"]
    assert seen["url"].endswith("/sendMessage")
    assert seen["data"]["chat_id"] == "456"
    assert seen["data"]["text"] == "olá"


def test_raises_when_telegram_reports_not_ok():
    resp = FakeResponse(200, {"ok": False, "description": "chat not found"})
    sender = TelegramSender("t", "c", poster=lambda u, d, t: resp)
    with pytest.raises(TelegramError, match="chat not found"):
        sender.send("x")


def test_raises_on_http_error_status():
    sender = TelegramSender("t", "c", poster=lambda u, d, t: FakeResponse(401, {"ok": False}))
    with pytest.raises(TelegramError, match="401"):
        sender.send("x")


def test_long_message_is_split_into_several_sends():
    calls = []

    def poster(url, data, timeout):
        calls.append(data["text"])
        return FakeResponse()

    # O limite do Telegram e 4096 caracteres por mensagem.
    sender = TelegramSender("t", "c", poster=poster)
    sender.send("a" * 9000)
    assert len(calls) == 3
    assert all(len(chunk) <= 4096 for chunk in calls)
    assert "".join(calls) == "a" * 9000


def test_token_is_never_included_in_the_error_text():
    sender = TelegramSender("SEGREDO", "c", poster=lambda u, d, t: FakeResponse(500, {"ok": False}))
    with pytest.raises(TelegramError) as caught:
        sender.send("x")
    assert "SEGREDO" not in str(caught.value)


def test_build_message_puts_question_then_answer():
    text = build_message("O que foi o Big Bang?", "Foi o evento inicial.", "Sapiens")
    assert text.index("O que foi o Big Bang?") < text.index("Foi o evento inicial.")
    assert "Sapiens" in text


def test_build_message_carries_the_reliability_warning():
    text = build_message("q", "a", None)
    # A resposta chega sem o usuario ter pedido naquele momento, entao vem com aviso.
    assert "sem verifica" in text.lower() or "confira" in text.lower()
