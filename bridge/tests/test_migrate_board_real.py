"""Board rewriting checked against the exact board this vault had.

Written after the migration produced an empty board and the cause turned out to
be that the board was already empty on disk. That ruled the migration out, but
only by inspection — this pins the behaviour against the real content so the
question cannot come back unanswered.
"""

from handy_bridge import migrate

# The board as it stood on 2026-09-09, before the vault was reorganised.
REAL_BOARD = """---

kanban-plugin: board

---

## Triagem
- [ ] Reler com mais calma o capítulo sobre a revolução cognitiva \
[[2026-09-08 1400 - Entendimento do Big Bang e revisão da revolução cognitiva]]
- [ ] Reler com mais calma o capítulo sobre a revolução cognitiva \
[[2026-09-08 1610 - Conceitos de Big Bang e Revolução Cognitiva]]

## A pesquisar

## Pesquisando

## Concluído
- [ ] Entender o que exatamente foi o Big Bang \
[[2026-09-08 1400 - Entendimento do Big Bang e revisão da revolução cognitiva]]
- [x] Entender o que exatamente foi o Big Bang \
[[2026-09-08 1610 - Conceitos de Big Bang e Revolução Cognitiva]]
- [x] Qual é a definição exata de física e química? Por que a física veio antes da \
química? [[2026-09-09 2205 - Definição e ordem entre física e química]]

%% kanban:settings
```
{"kanban-plugin":"board"}
```
%%
"""

RENAMES = {
    "2026-09-08 1400 - Entendimento do Big Bang e revisão da revolução cognitiva":
        "03-008120 Entendimento do Big Bang e revisão da revolução cognitiva",
    "2026-09-08 1610 - Conceitos de Big Bang e Revolução Cognitiva":
        "03-008120 Conceitos de Big Bang e Revolução Cognitiva",
    "2026-09-09 2205 - Definição e ordem entre física e química":
        "03-001324 Definição e ordem entre física e química",
}


def test_the_real_board_keeps_three_cards_after_dedupe():
    out = migrate.rewrite_board(REAL_BOARD, RENAMES)
    cards = [line for line in out.splitlines() if line.startswith("- [")]
    assert len(cards) == 3


def test_the_duplicated_big_bang_card_keeps_the_checked_copy():
    out = migrate.rewrite_board(REAL_BOARD, RENAMES)
    big_bang = [line for line in out.splitlines() if "Entender o que exatamente" in line]
    assert len(big_bang) == 1
    assert big_bang[0].startswith("- [x]")


def test_the_duplicated_triage_card_collapses_to_one():
    out = migrate.rewrite_board(REAL_BOARD, RENAMES)
    reler = [line for line in out.splitlines() if "Reler com mais calma" in line]
    assert len(reler) == 1


def test_every_card_lands_in_the_lane_it_came_from():
    lines = migrate.rewrite_board(REAL_BOARD, RENAMES).splitlines()

    def lane(name: str) -> list[str]:
        at = lines.index(f"## {name}")
        out = []
        for line in lines[at + 1 :]:
            if line.startswith("## ") or line.startswith("%%"):
                break
            if line.startswith("- ["):
                out.append(line)
        return out

    assert len(lane("Triagem")) == 1
    assert lane("A pesquisar") == []
    assert lane("Pesquisando") == []
    assert len(lane("Concluído")) == 2


def test_every_link_is_rewritten_to_the_new_note_name():
    out = migrate.rewrite_board(REAL_BOARD, RENAMES)
    assert "2026-09-0" not in out
    assert "[[03-008120 Conceitos de Big Bang e Revolução Cognitiva]]" in out


def test_the_settings_block_survives_byte_for_byte():
    out = migrate.rewrite_board(REAL_BOARD, RENAMES)
    assert '%% kanban:settings\n```\n{"kanban-plugin":"board"}\n```\n%%' in out


def test_an_already_empty_board_is_left_with_its_lanes():
    # O caso que de fato aconteceu no vault: o quadro chegou vazio na migracao.
    empty = REAL_BOARD.split("## Triagem")[0] + "## Triagem\n\n## Concluído\n"
    out = migrate.rewrite_board(empty, {})
    assert "## Triagem" in out and "## Concluído" in out
