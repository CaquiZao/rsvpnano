from datetime import datetime
from pathlib import Path

from handy_bridge import kanban
from handy_bridge.digest import (
    DigestState,
    collect_pending,
    render_digest,
    should_send,
)


def board_with(tmp_path: Path, name: str, cards: dict[str, list[str]]) -> Path:
    path = kanban.ensure_board(tmp_path / "Quadros" / f"{name}.md")
    for lane, items in cards.items():
        kanban.add_cards(path, lane, items)
    return path


# --- coleta ---------------------------------------------------------------


def test_collects_open_cards_from_triage_and_todo(tmp_path):
    board_with(
        tmp_path,
        "sapiens",
        {
            kanban.TRIAGE_LANE: ["reler o capitulo 5"],
            kanban.TODO_LANE: ["pesquisar o Big Bang"],
        },
    )
    pending = collect_pending(tmp_path, "Quadros")
    assert pending["sapiens"][kanban.TRIAGE_LANE] == ["reler o capitulo 5"]
    assert pending["sapiens"][kanban.TODO_LANE] == ["pesquisar o Big Bang"]


def test_ignores_the_done_and_doing_lanes(tmp_path):
    board_with(
        tmp_path,
        "sapiens",
        {
            kanban.DOING_LANE: ["ja estou nisso"],
            kanban.DONE_LANE: ["resolvida"],
        },
    )
    assert collect_pending(tmp_path, "Quadros") == {}


def test_checked_cards_are_not_pending(tmp_path):
    path = board_with(tmp_path, "sapiens", {kanban.TRIAGE_LANE: ["aberta"]})
    kanban.add_cards(path, kanban.TRIAGE_LANE, [kanban.render_card("fechada", None, done=True)])
    pending = collect_pending(tmp_path, "Quadros")
    assert pending["sapiens"][kanban.TRIAGE_LANE] == ["aberta"]


def test_the_wikilink_is_stripped_from_the_card_text(tmp_path):
    board_with(
        tmp_path,
        "sapiens",
        {kanban.TRIAGE_LANE: [kanban.render_card("pesquisar isso", "2026-09-08 1400 - Nota")]},
    )
    pending = collect_pending(tmp_path, "Quadros")
    assert pending["sapiens"][kanban.TRIAGE_LANE] == ["pesquisar isso"]


def test_no_boards_means_nothing_pending(tmp_path):
    assert collect_pending(tmp_path, "Quadros") == {}


def test_several_boards_are_reported_separately(tmp_path):
    board_with(tmp_path, "sapiens", {kanban.TRIAGE_LANE: ["a"]})
    board_with(tmp_path, "Geral", {kanban.TRIAGE_LANE: ["b"]})
    pending = collect_pending(tmp_path, "Quadros")
    assert set(pending) == {"sapiens", "Geral"}


# --- renderizacao ---------------------------------------------------------


def test_render_lists_each_board_and_lane():
    text = render_digest({"sapiens": {kanban.TRIAGE_LANE: ["reler o cap 5"]}})
    assert "sapiens" in text
    assert kanban.TRIAGE_LANE in text
    assert "reler o cap 5" in text


def test_render_caps_a_long_list_and_says_how_many_were_hidden():
    many = [f"tarefa {n}" for n in range(20)]
    text = render_digest({"livro": {kanban.TRIAGE_LANE: many}}, max_per_lane=5)
    assert "tarefa 0" in text
    assert "tarefa 19" not in text
    assert "15" in text  # quantas ficaram de fora


def test_render_of_nothing_says_so():
    text = render_digest({})
    assert "nada" in text.lower() or "nenhuma" in text.lower()


# --- agendamento ----------------------------------------------------------


def test_sends_on_the_configured_weekday_and_hour():
    # Domingo, 2026-09-13, as 19h
    now = datetime(2026, 9, 13, 19, 5)
    assert should_send(now, last_sent=None, weekday=6, hour=19) is True


def test_does_not_send_before_the_hour():
    now = datetime(2026, 9, 13, 18, 59)
    assert should_send(now, last_sent=None, weekday=6, hour=19) is False


def test_does_not_send_on_another_weekday():
    now = datetime(2026, 9, 14, 20, 0)  # segunda
    assert should_send(now, last_sent=None, weekday=6, hour=19) is False


def test_does_not_send_twice_on_the_same_day():
    now = datetime(2026, 9, 13, 21, 0)
    already = datetime(2026, 9, 13, 19, 2)
    assert should_send(now, last_sent=already, weekday=6, hour=19) is False


def test_sends_again_the_following_week():
    now = datetime(2026, 9, 20, 19, 0)
    last = datetime(2026, 9, 13, 19, 0)
    assert should_send(now, last_sent=last, weekday=6, hour=19) is True


def test_a_missed_window_still_sends_later_that_day():
    # PC desligado as 19h; ligou as 23h. Melhor tarde que nunca.
    now = datetime(2026, 9, 13, 23, 30)
    assert should_send(now, last_sent=None, weekday=6, hour=19) is True


# --- estado ---------------------------------------------------------------


def test_state_survives_a_restart(tmp_path):
    path = tmp_path / "digest.json"
    DigestState(path).record(datetime(2026, 9, 13, 19, 0))
    assert DigestState(path).last_sent() == datetime(2026, 9, 13, 19, 0)


def test_a_fresh_state_has_never_sent(tmp_path):
    assert DigestState(tmp_path / "digest.json").last_sent() is None


def test_a_corrupt_state_file_is_treated_as_never_sent(tmp_path):
    path = tmp_path / "digest.json"
    path.write_text("{quebrado", encoding="utf-8")
    assert DigestState(path).last_sent() is None


# --- agendador ------------------------------------------------------------

from handy_bridge.digest import DigestScheduler  # noqa: E402


class FakeTelegram:
    def __init__(self):
        self.sent = []

    def send(self, text, reply_to=None):
        self.sent.append(text)
        return 1


class FakeCfg:
    def __init__(self, tmp_path, weekday=6, hour=19):
        from handy_bridge.config import DigestConfig, KanbanConfig

        self.vault_path = tmp_path
        self.kanban = KanbanConfig(enabled=True, subfolder="Quadros")
        self.digest = DigestConfig(enabled=True, weekday=weekday, hour=hour)


def test_tick_sends_inside_the_window_and_records_it(tmp_path):
    board_with(tmp_path, "sapiens", {kanban.TRIAGE_LANE: ["reler o cap 5"]})
    state = DigestState(tmp_path / "d.json")
    tg = FakeTelegram()
    sch = DigestScheduler(FakeCfg(tmp_path), tg, state)

    assert sch.tick(datetime(2026, 9, 13, 19, 5)) is True
    assert "reler o cap 5" in tg.sent[0]
    assert state.last_sent() is not None


def test_tick_outside_the_window_sends_nothing(tmp_path):
    tg = FakeTelegram()
    sch = DigestScheduler(FakeCfg(tmp_path), tg, DigestState(tmp_path / "d.json"))
    assert sch.tick(datetime(2026, 9, 14, 19, 5)) is False
    assert tg.sent == []


def test_tick_does_not_repeat_on_the_same_day(tmp_path):
    tg = FakeTelegram()
    sch = DigestScheduler(FakeCfg(tmp_path), tg, DigestState(tmp_path / "d.json"))
    sch.tick(datetime(2026, 9, 13, 19, 5))
    sch.tick(datetime(2026, 9, 13, 22, 0))
    assert len(tg.sent) == 1


def test_an_empty_week_still_reports(tmp_path):
    tg = FakeTelegram()
    sch = DigestScheduler(FakeCfg(tmp_path), tg, DigestState(tmp_path / "d.json"))
    sch.tick(datetime(2026, 9, 13, 19, 5))
    assert "Nada em aberto" in tg.sent[0]
