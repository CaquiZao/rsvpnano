import pytest

from handy_bridge.telegram import (
    MAX_MESSAGE_CHARS,
    TelegramError,
    TelegramSender,
    build_failure,
    build_message,
)


class FakeResponse:
    def __init__(self, status: int = 200, payload: dict | None = None):
        self.status_code = status
        self._payload = (
            payload if payload is not None else {"ok": True, "result": {"message_id": 42}}
        )

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


def test_send_returns_the_message_id_of_the_first_chunk():
    sender = TelegramSender("t", "c", poster=lambda u, d, ti: FakeResponse())
    assert sender.send("oi") == 42


def test_reply_to_is_attached_only_to_the_first_chunk():
    seen = []
    sender = TelegramSender("t", "c", poster=lambda u, d, ti: (seen.append(d), FakeResponse())[1])
    sender.send("a" * 5000, reply_to=7)
    assert seen[0]["reply_to_message_id"] == 7
    assert "reply_to_message_id" not in seen[1]


def test_poll_returns_the_updates():
    resp = FakeResponse(200, {"ok": True, "result": [{"update_id": 1}, {"update_id": 2}]})
    got = TelegramSender("t", "c", poster=lambda u, d, ti: resp).poll(offset=0)
    assert [u["update_id"] for u in got] == [1, 2]


def test_poll_passes_the_offset_so_updates_are_not_reprocessed():
    seen = {}
    resp = FakeResponse(200, {"ok": True, "result": []})

    def poster(url, data, timeout):
        seen["url"] = url
        seen.update(data)
        return resp

    TelegramSender("t", "c", poster=poster).poll(offset=99)
    assert seen["offset"] == 99
    assert seen["url"].endswith("/getUpdates")


# --- aviso de chegada -------------------------------------------------------


def test_build_arrival_names_the_kind_and_the_title():
    from handy_bridge.telegram import build_arrival

    got = build_arrival("recall", "Tres revolucoes", "o que eu falei")
    assert got.startswith("🔁 Recall — Tres revolucoes")
    assert "o que eu falei" in got


def test_build_arrival_does_not_repeat_the_transcript_as_a_cleaned_body():
    from handy_bridge.telegram import build_arrival

    # O corpo limpo e a mesma fala arrumada, e no celular nao ha como recolher
    # nada: mandar os dois faz a pessoa ler a mesma coisa duas vezes.
    got = build_arrival("recall", "T", "houveram tres revolucoes")
    assert got.count("houveram tres revolucoes") == 1


def test_build_arrival_puts_the_transcript_after_the_discussion():
    from handy_bridge.telegram import build_arrival

    got = build_arrival("recall", "T", "FALADO", reasoning="RACIOCINIO")
    # A transcricao e referencia: vem depois do que foi trabalhado, como na nota.
    assert got.index("RACIOCINIO") < got.index("FALADO")


def test_build_arrival_carries_the_recall_discussion():
    from handy_bridge.telegram import build_arrival

    got = build_arrival(
        "recall", "T", "falado",
        reasoning="Seu instinto acertou a aceleracao.",
        deepening="A Belle Epoque foi o apice.",
    )
    assert "Seu instinto acertou" in got
    assert "Belle Epoque" in got


def test_build_arrival_cuts_a_long_transcript_and_says_so():
    from handy_bridge.telegram import MAX_TRANSCRIPT_CHARS, build_arrival

    got = build_arrival("anotação", "T", "x" * (MAX_TRANSCRIPT_CHARS + 500))
    assert "transcrição cortada" in got
    assert len(got) < MAX_TRANSCRIPT_CHARS + 400


def test_build_arrival_warns_that_answers_are_coming():
    from handy_bridge.telegram import build_arrival

    um = build_arrival("pergunta", "T", "f", answers_coming=1)
    dois = build_arrival("pergunta", "T", "f", answers_coming=2)
    assert "1 resposta chegando" in um
    assert "2 respostas chegando" in dois
    # Sem resposta a caminho, nada de promessa que nao se cumpre.
    assert "chegando" not in build_arrival("anotação", "T", "f")


def test_build_message_without_a_question_renders_answer_only():
    from handy_bridge.telegram import build_message

    # Acontece quando a pergunta era a gravacao inteira: o aviso de chegada acabou
    # de mostrar aquele texto, e repeti-lo na resposta imprime a fala duas vezes.
    got = build_message("", "A resposta.", "Sapiens")
    assert "❓" not in got
    assert got.startswith("A resposta.")
    assert "📖 Sapiens" in got


def test_build_message_keeps_a_real_question():
    from handy_bridge.telegram import build_message

    got = build_message("O que foi o Big Bang?", "O evento inicial.", None)
    assert got.startswith("❓ O que foi o Big Bang?")


def test_failure_notice_says_what_broke_and_that_it_will_retry():
    text = build_failure(
        "boot-0004-00033920", "Handy failed with exit code 3221225477", 1, will_retry=True
    )
    assert "boot-0004-00033920" in text
    assert "3221225477" in text
    # The point of the message: the recording still exists.
    assert "guardado" in text.lower()
    assert "de novo" in text.lower()


def test_failure_notice_stops_promising_a_retry_once_it_gave_up():
    text = build_failure("boot-0004-00033920", "sem VRAM", 3, will_retry=False)
    assert "de novo" not in text.lower()
    assert "3" in text
    assert "guardado" in text.lower()


def test_failure_notice_trims_a_giant_reason():
    text = build_failure("n1", "x" * 5000, 1, will_retry=True)
    assert len(text) < MAX_MESSAGE_CHARS
