from pathlib import Path

from handy_bridge.threads import ThreadStore


def test_records_and_resolves_a_message_to_its_note(tmp_path):
    store = ThreadStore(tmp_path / "threads.json")
    store.remember(100, Path("/vault/Inbox/nota.md"), "O que foi o Big Bang?", "O evento inicial.")
    thread = store.resolve(100)
    assert thread is not None
    assert thread.note_path == Path("/vault/Inbox/nota.md")
    assert thread.history == [("O que foi o Big Bang?", "O evento inicial.")]


def test_unknown_message_resolves_to_none(tmp_path):
    assert ThreadStore(tmp_path / "t.json").resolve(999) is None


def test_latest_returns_the_most_recent_thread(tmp_path):
    store = ThreadStore(tmp_path / "t.json")
    store.remember(1, Path("/a.md"), "q1", "a1")
    store.remember(2, Path("/b.md"), "q2", "a2")
    latest = store.latest()
    assert latest is not None
    assert latest.note_path == Path("/b.md")


def test_latest_is_none_on_a_fresh_store(tmp_path):
    assert ThreadStore(tmp_path / "t.json").latest() is None


def test_history_accumulates_across_a_thread(tmp_path):
    store = ThreadStore(tmp_path / "t.json")
    store.remember(1, Path("/a.md"), "q1", "a1")
    store.append(1, "q2", "a2", new_message_id=2)
    # Responder a qualquer uma das mensagens da thread da o mesmo historico
    for mid in (1, 2):
        thread = store.resolve(mid)
        assert thread.history == [("q1", "a1"), ("q2", "a2")]


def test_append_to_an_unknown_thread_is_ignored(tmp_path):
    store = ThreadStore(tmp_path / "t.json")
    store.append(42, "q", "a", new_message_id=43)
    assert store.resolve(43) is None


def test_state_survives_a_restart(tmp_path):
    path = tmp_path / "t.json"
    ThreadStore(path).remember(7, Path("/vault/n.md"), "q", "a")
    reopened = ThreadStore(path)
    assert reopened.resolve(7).note_path == Path("/vault/n.md")
    assert reopened.latest().note_path == Path("/vault/n.md")


def test_history_is_capped_so_the_prompt_cannot_grow_without_bound(tmp_path):
    store = ThreadStore(tmp_path / "t.json", max_history=3)
    store.remember(1, Path("/a.md"), "q0", "a0")
    for n in range(1, 6):
        store.append(1, f"q{n}", f"a{n}", new_message_id=1 + n)
    history = store.resolve(1).history
    assert len(history) == 3
    assert history[-1] == ("q5", "a5")


def test_a_corrupt_state_file_does_not_crash_the_store(tmp_path):
    path = tmp_path / "t.json"
    path.write_text("{isso nao e json", encoding="utf-8")
    store = ThreadStore(path)
    assert store.latest() is None
    # E continua utilizavel depois
    store.remember(1, Path("/a.md"), "q", "a")
    assert store.resolve(1) is not None
