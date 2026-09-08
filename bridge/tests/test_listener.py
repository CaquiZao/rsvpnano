from pathlib import Path

from handy_bridge.listener import TelegramListener
from handy_bridge.postprocess import PostProcessError
from handy_bridge.threads import ThreadStore


class FakeTelegram:
    def __init__(self, updates: list[dict]):
        self._updates = updates
        self.sent: list[tuple[str, int | None]] = []
        self.polls: list[int] = []
        self._next_id = 500

    def poll(self, offset: int, timeout_s: int = 25) -> list[dict]:
        self.polls.append(offset)
        batch, self._updates = self._updates, []
        return batch

    def send(self, text: str, reply_to: int | None = None) -> int:
        self.sent.append((text, reply_to))
        self._next_id += 1
        return self._next_id


class FakeProcessor:
    def __init__(self, answer: str = "resposta nova", error: Exception | None = None):
        self.answer = answer
        self.error = error
        self.calls: list[tuple[str, list, str | None]] = []

    def answer_followup(self, question, history, excerpt):
        self.calls.append((question, list(history), excerpt))
        if self.error:
            raise self.error
        return self.answer


def message(text: str, chat_id: int = 7, update_id: int = 1, reply_to: int | None = None) -> dict:
    msg = {"message_id": 900 + update_id, "chat": {"id": chat_id}, "text": text}
    if reply_to is not None:
        msg["reply_to_message"] = {"message_id": reply_to}
    return {"update_id": update_id, "message": msg}


def make_note(tmp_path: Path, name: str = "nota.md") -> Path:
    path = tmp_path / name
    path.write_text(
        "---\ntitle: x\n---\n\ncorpo\n\n> [!note]- Transcrição original\n> cru\n",
        encoding="utf-8",
    )
    return path


def build(tmp_path, updates, *, chat_id=7, processor=None, seed=True):
    store = ThreadStore(tmp_path / "t.json")
    note = make_note(tmp_path)
    if seed:
        store.remember(100, note, "O que foi o Big Bang?", "O evento inicial.")
    tg = FakeTelegram(updates)
    proc = processor or FakeProcessor()
    listener = TelegramListener(tg, proc, store, allowed_chat_id=str(chat_id))
    return listener, tg, proc, store, note


def test_reply_to_a_known_message_answers_with_that_thread(tmp_path):
    listener, tg, proc, _, _ = build(tmp_path, [message("e a singularidade?", reply_to=100)])
    listener.poll_once()

    assert proc.calls[0][0] == "e a singularidade?"
    assert proc.calls[0][1] == [("O que foi o Big Bang?", "O evento inicial.")]
    assert tg.sent[0][0] == "resposta nova"
    assert tg.sent[0][1] == 901  # responde citando a mensagem do usuario


def test_the_answer_is_appended_to_the_originating_note(tmp_path):
    listener, _, _, _, note = build(tmp_path, [message("e a singularidade?", reply_to=100)])
    listener.poll_once()
    text = note.read_text(encoding="utf-8")
    assert "> [!question] e a singularidade?" in text
    assert "> resposta nova" in text
    assert text.index("[!question]") < text.index("Transcrição original")


def test_a_bare_message_uses_the_most_recent_thread(tmp_path):
    listener, _, proc, _, _ = build(tmp_path, [message("explica melhor")])
    listener.poll_once()
    assert proc.calls[0][1] == [("O que foi o Big Bang?", "O evento inicial.")]


def test_a_bare_message_with_no_history_still_gets_an_answer(tmp_path):
    listener, tg, proc, _, _ = build(tmp_path, [message("o que e entropia?")], seed=False)
    listener.poll_once()
    assert proc.calls[0][1] == []
    assert tg.sent


def test_a_message_from_another_chat_is_ignored(tmp_path):
    listener, tg, proc, _, _ = build(tmp_path, [message("me responde", chat_id=999)])
    listener.poll_once()
    # O nome do bot e publico: responder a estranhos gastaria os limites do dono.
    assert proc.calls == []
    assert tg.sent == []


def test_the_followup_lands_in_the_thread_history(tmp_path):
    listener, _, _, store, _ = build(tmp_path, [message("e a singularidade?", reply_to=100)])
    listener.poll_once()
    assert store.resolve(100).history[-1] == ("e a singularidade?", "resposta nova")


def test_offset_advances_so_an_update_is_not_answered_twice(tmp_path):
    listener, tg, _, _, _ = build(tmp_path, [message("oi", update_id=41)])
    listener.poll_once()
    listener.poll_once()
    assert tg.polls == [0, 42]
    assert len(tg.sent) == 1


def test_a_failing_answer_tells_the_user_instead_of_going_silent(tmp_path):
    proc = FakeProcessor(error=PostProcessError("modelo caiu"))
    listener, tg, _, _, _ = build(tmp_path, [message("q", reply_to=100)], processor=proc)
    listener.poll_once()
    assert len(tg.sent) == 1
    assert "não consegui" in tg.sent[0][0].lower()


def test_updates_without_text_are_skipped(tmp_path):
    sticker = {"update_id": 3, "message": {"message_id": 903, "chat": {"id": 7}}}
    listener, tg, proc, _, _ = build(tmp_path, [sticker])
    listener.poll_once()
    assert proc.calls == []
    assert tg.sent == []


def test_a_missing_note_file_does_not_crash_the_listener(tmp_path):
    listener, tg, _, _, note = build(tmp_path, [message("q", reply_to=100)])
    note.unlink()
    listener.poll_once()
    # A resposta ainda chega no Telegram, mesmo sem conseguir gravar na nota
    assert tg.sent
