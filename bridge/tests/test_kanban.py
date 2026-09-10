from pathlib import Path

import pytest

from handy_bridge.kanban import (
    LANES,
    KanbanError,
    add_cards,
    board_path_for,
    ensure_board,
    render_card,
)


def test_ensure_board_creates_plugin_compatible_file(tmp_path):
    path = ensure_board(tmp_path / "Quadros" / "livro.md")
    text = path.read_text(encoding="utf-8")
    # Formato extraido do proprio codigo do plugin (obsidian-kanban 2.0.51)
    assert text.startswith("---\n\nkanban-plugin: board\n\n---\n")
    for lane in LANES:
        assert f"## {lane}" in text
    assert "%% kanban:settings" in text


def test_ensure_board_is_idempotent(tmp_path):
    path = ensure_board(tmp_path / "b.md")
    path.write_text(path.read_text(encoding="utf-8") + "\n<!-- meu -->\n", encoding="utf-8")
    again = ensure_board(tmp_path / "b.md")
    assert "<!-- meu -->" in again.read_text(encoding="utf-8")


def test_add_card_lands_under_the_requested_lane(tmp_path):
    path = ensure_board(tmp_path / "b.md")
    add_cards(path, "A pesquisar", ["Pesquisar o Big Bang"])
    lines = path.read_text(encoding="utf-8").splitlines()

    at = lines.index("## A pesquisar")
    following = [ln for ln in lines[at + 1 :] if ln.strip()]
    assert following[0] == "- [ ] Pesquisar o Big Bang"


def test_add_card_never_touches_the_settings_block(tmp_path):
    path = ensure_board(tmp_path / "b.md")
    before = path.read_text(encoding="utf-8")
    settings_at = before.index("%% kanban:settings")
    tail_before = before[settings_at:]

    add_cards(path, "Triagem", ["uma", "outra"])
    after = path.read_text(encoding="utf-8")
    assert after[after.index("%% kanban:settings") :] == tail_before


def test_cards_append_after_existing_ones_in_the_same_lane(tmp_path):
    path = ensure_board(tmp_path / "b.md")
    add_cards(path, "Triagem", ["primeira"])
    add_cards(path, "Triagem", ["segunda"])
    lines = path.read_text(encoding="utf-8").splitlines()
    at = lines.index("## Triagem")
    cards = [ln for ln in lines[at + 1 :] if ln.startswith("- [ ]")]
    assert cards[:2] == ["- [ ] primeira", "- [ ] segunda"]


def test_adding_to_a_missing_lane_creates_it_before_the_settings(tmp_path):
    path = ensure_board(tmp_path / "b.md")
    add_cards(path, "Raia Nova", ["x"])
    text = path.read_text(encoding="utf-8")
    assert "## Raia Nova" in text
    assert text.index("## Raia Nova") < text.index("%% kanban:settings")


def test_empty_card_list_leaves_the_file_untouched(tmp_path):
    path = ensure_board(tmp_path / "b.md")
    before = path.read_bytes()
    add_cards(path, "Triagem", [])
    assert path.read_bytes() == before


def test_render_card_links_back_to_the_note():
    card = render_card("Pesquisar o Big Bang", "2026-09-08 1215 - Minha nota")
    assert card == "- [ ] Pesquisar o Big Bang [[2026-09-08 1215 - Minha nota]]"


def test_render_card_without_note_has_no_link():
    assert render_card("Só a tarefa", None) == "- [ ] Só a tarefa"


def test_render_card_strips_a_leading_checkbox_from_the_model():
    # O LLM as vezes devolve "- [ ] texto"; nao queremos duplicar o marcador.
    assert render_card("- [ ] Já vem marcado", None) == "- [ ] Já vem marcado"


def test_add_cards_accepts_raw_text_or_rendered_lines(tmp_path):
    # A caller that forgets render_card() must still produce a valid board line,
    # because a malformed line silently breaks the user's board.
    path = ensure_board(tmp_path / "b.md")
    add_cards(path, "Triagem", ["texto cru", render_card("com link", "Nota X")])
    cards = [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.startswith("- [ ]")]
    assert cards == ["- [ ] texto cru", "- [ ] com link [[Nota X]]"]


def test_board_path_uses_the_book_stem(tmp_path):
    got = board_path_for(tmp_path, "Quadros", "epdf.pub_sapiens")
    # O quadro mora dentro do diretorio do livro, junto das notas e resumos.
    assert got == tmp_path / "Livros" / "epdf.pub_sapiens" / "Quadro.md"


def test_board_path_falls_back_to_a_general_board(tmp_path):
    got = board_path_for(tmp_path, "Quadros", None)
    assert got == tmp_path / "Geral" / "Quadro.md"


def test_add_cards_rejects_a_file_that_is_not_a_board(tmp_path):
    stray = tmp_path / "nao-e-quadro.md"
    stray.write_text("# só uma nota\n", encoding="utf-8")
    with pytest.raises(KanbanError, match="not a Kanban board"):
        add_cards(stray, "Triagem", ["x"])


def test_add_cards_leaves_no_temp_file(tmp_path):
    path = ensure_board(tmp_path / "b.md")
    add_cards(path, "Triagem", ["x"])
    assert [p.name for p in Path(tmp_path).iterdir() if p.suffix != ".md"] == []


def test_render_card_marks_done_when_asked():
    # Um cartao na raia Concluido precisa vir marcado; desmarcado ali e contraditorio.
    assert render_card("Resolvida", None, done=True) == "- [x] Resolvida"
    assert render_card("Resolvida", "Nota", done=True) == "- [x] Resolvida [[Nota]]"


def test_add_cards_preserves_a_checked_line(tmp_path):
    path = ensure_board(tmp_path / "b.md")
    add_cards(path, "Concluído", [render_card("Feita", None, done=True)])
    assert "- [x] Feita" in path.read_text(encoding="utf-8")
