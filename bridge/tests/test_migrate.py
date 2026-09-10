from pathlib import Path

from handy_bridge import migrate
from test_epub import make_epub_with

CH_ONE = ("c1.xhtml", "<h1>Um</h1><p>o comeco do livro fala de outras coisas quaisquer</p>")
CH_TWO = (
    "c2.xhtml",
    "<h1>Dois</h1><p>humanos chegaram a ilha de Flores quando o nivel do mar"
    " estava excepcionalmente baixo</p>",
)

NOTE = """---
title: "Crítica à tese"
date: 2026-09-08T12:05:00
duration: 21s
source: rsvp-nano
asr_model: nemotron
tags: [leitura]
book: "[[livro]]"
word_offset: 1324
---

Texto limpo da nota.

> [!quote] Trecho que eu estava lendo
> humanos chegaram a ilha de Flores quando o nivel do mar

> [!note]- Transcrição original
> transcricao literal aqui
"""

BOARD = """---

kanban-plugin: board

---

## Triagem
- [ ] Reler o capítulo [[2026-09-08 1205 - Crítica à tese]]

## A pesquisar

## Pesquisando

## Concluído
- [ ] Entender o Big Bang [[2026-09-08 1205 - Crítica à tese]]
- [x] Entender o Big Bang [[2026-09-08 1215 - Outra nota]]

%% kanban:settings
```
{"kanban-plugin":"board"}
```
%%
"""


def legacy_vault(tmp_path, *, board=BOARD, notes=None) -> Path:
    """A vault shaped the way it was before this reorganisation."""
    vault = tmp_path / "Reading"
    inbox = vault / "Inbox" / "livro"
    inbox.mkdir(parents=True)
    for name, text in (notes or {"2026-09-08 1205 - Crítica à tese.md": NOTE}).items():
        (inbox / name).write_text(text, encoding="utf-8")

    (vault / "Quadros").mkdir()
    (vault / "Quadros" / "livro.md").write_text(board, encoding="utf-8")
    (vault / "Books").mkdir()
    (vault / "Books" / "livro.md").write_text("# Um\n\ntexto\n", encoding="utf-8")
    make_epub_with(vault / "livro.epub", [CH_ONE, CH_TWO])
    return vault


def test_migration_moves_notes_into_the_book_layout(tmp_path):
    vault = legacy_vault(tmp_path)
    migrate.apply_migration(vault, dry_run=False)

    notes = list((vault / "Livros" / "livro" / "Anotações").glob("*.md"))
    assert len(notes) == 1
    # Renomeada para a posicao de leitura, com o capitulo resolvido pelo trecho.
    assert notes[0].name == "2026-09-08 1205 - Crítica à tese.md"
    assert (vault / "Livros" / "livro" / "Quadro.md").is_file()
    assert (vault / "Livros" / "livro" / "fonte" / "livro.epub").is_file()
    assert (vault / "Livros" / "livro" / "fonte" / "livro.md").is_file()


def test_migration_fills_in_the_new_frontmatter(tmp_path):
    vault = legacy_vault(tmp_path)
    migrate.apply_migration(vault, dry_run=False)
    text = next((vault / "Livros" / "livro" / "Anotações").glob("*.md")).read_text(
        encoding="utf-8"
    )
    assert "kind: anotação" in text
    assert "chapter: 2" in text
    assert 'chapter_title: "Dois"' in text
    assert "chapter_source: exato" in text
    # O conteudo original segue intacto.
    assert "Texto limpo da nota." in text
    assert "transcricao literal aqui" in text


def test_a_note_with_answered_questions_is_classified_as_a_question(tmp_path):
    # Nao e inferencia: o bridge ja decidiu, na epoca, que ali havia pergunta
    # respondivel, e gravou a resposta num callout.
    with_question = NOTE.replace(
        "> [!note]- Transcrição original",
        "> [!question] O que define a fisica?\n> Estuda materia e energia.\n\n"
        "> [!note]- Transcrição original",
    )
    vault = legacy_vault(tmp_path, notes={"nota.md": with_question})
    migrate.apply_migration(vault, dry_run=False)
    text = next((vault / "Livros" / "livro" / "Perguntas").glob("*.md")).read_text(
        encoding="utf-8"
    )
    assert "kind: pergunta" in text


def test_a_spoken_recall_marker_in_the_old_transcript_still_wins(tmp_path):
    spoken = NOTE.replace("> transcricao literal aqui", "> recapitulando, foi assim")
    vault = legacy_vault(tmp_path, notes={"nota.md": spoken})
    migrate.apply_migration(vault, dry_run=False)
    text = next((vault / "Livros" / "livro" / "Recall").glob("*.md")).read_text(
        encoding="utf-8"
    )
    assert "kind: recall" in text


def test_migration_dedupes_identical_cards_keeping_the_checked_one(tmp_path):
    vault = legacy_vault(tmp_path)
    migrate.apply_migration(vault, dry_run=False)
    board = (vault / "Livros" / "livro" / "Quadro.md").read_text(encoding="utf-8")
    assert board.count("Entender o Big Bang") == 1
    # Um cartao concluido perdido reabre trabalho feito; o inverso custa um clique.
    assert "- [x] Entender o Big Bang" in board


def test_migration_keeps_every_card_pointing_at_a_note_that_exists(tmp_path):
    # O invariante que importa, e nao um nome especifico: seja qual for a
    # convencao de nome, um cartao nao pode virar link quebrado.
    import re

    vault = legacy_vault(tmp_path)
    migrate.apply_migration(vault, dry_run=False)
    board = (vault / "Livros" / "livro" / "Quadro.md").read_text(encoding="utf-8")

    book = vault / "Livros" / "livro"
    existing = {p.stem for p in book.rglob("*.md")}
    linked = set(re.findall(r"\[\[([^\]]+)\]\]", board))
    # "Outra nota" nunca existiu como arquivo, so como link no quadro de origem.
    assert linked & existing
    assert "Crítica à tese" in " ".join(linked)


def test_migration_renames_a_position_coded_note_to_its_date(tmp_path):
    # O nome codificado por posicao foi uma etapa intermediaria; migrar de novo
    # tem de trazer a nota para o nome por data.
    vault = tmp_path / "Reading"
    notas = vault / "Livros" / "livro" / "Notas"
    notas.mkdir(parents=True)
    (notas / "08-012438 Crítica à tese.md").write_text(NOTE, encoding="utf-8")
    migrate.apply_migration(vault, dry_run=False)
    got = list((vault / "Livros" / "livro" / "Anotações").glob("*.md"))
    assert [p.name for p in got] == ["2026-09-08 1205 - Crítica à tese.md"]


def test_migration_keeps_the_kanban_settings_block_untouched(tmp_path):
    vault = legacy_vault(tmp_path)
    migrate.apply_migration(vault, dry_run=False)
    board = (vault / "Livros" / "livro" / "Quadro.md").read_text(encoding="utf-8")
    assert '{"kanban-plugin":"board"}' in board
    assert board.rstrip().endswith("%%")


def test_migration_removes_the_bases_it_replaced(tmp_path):
    # As views de Bases deram lugar a uma pasta por tipo. Apagar e seguro: elas nao
    # guardavam conteudo, so uma consulta sobre o frontmatter das notas.
    vault = legacy_vault(tmp_path)
    (vault / "Recall.base").write_text(
        "views:\n  - type: table\n", encoding="utf-8"
    )
    migrate.apply_migration(vault, dry_run=False)
    assert not (vault / "Recall.base").exists()


def test_dry_run_changes_nothing_on_disk(tmp_path):
    vault = legacy_vault(tmp_path)
    before = sorted(p.relative_to(vault).as_posix() for p in vault.rglob("*"))
    moves = migrate.apply_migration(vault, dry_run=True)
    after = sorted(p.relative_to(vault).as_posix() for p in vault.rglob("*"))
    assert moves
    assert before == after


def test_migration_is_idempotent(tmp_path):
    vault = legacy_vault(tmp_path)
    migrate.apply_migration(vault, dry_run=False)
    snapshot = sorted(p.relative_to(vault).as_posix() for p in vault.rglob("*"))
    assert migrate.apply_migration(vault, dry_run=False) == []
    assert sorted(p.relative_to(vault).as_posix() for p in vault.rglob("*")) == snapshot


def test_migration_removes_the_old_directories_when_they_empty_out(tmp_path):
    vault = legacy_vault(tmp_path)
    migrate.apply_migration(vault, dry_run=False)
    assert not (vault / "Inbox").exists()
    assert not (vault / "Quadros").exists()
    assert not (vault / "Books").exists()


def test_migration_keeps_a_directory_it_could_not_classify(tmp_path):
    vault = legacy_vault(tmp_path)
    (vault / "Inbox" / "solto.txt").write_text("nao sei o que e isso", encoding="utf-8")
    migrate.apply_migration(vault, dry_run=False)
    # Migracao nao apaga o que nao entendeu.
    assert (vault / "Inbox" / "solto.txt").is_file()


def test_migration_without_an_epub_still_moves_the_notes(tmp_path):
    vault = legacy_vault(tmp_path)
    (vault / "livro.epub").unlink()
    migrate.apply_migration(vault, dry_run=False)
    notes = list((vault / "Livros" / "livro" / "Anotações").glob("*.md"))
    assert len(notes) == 1
    # Sem livro nao ha capitulo: o prefixo cai para zero e a nota nao se perde.
    assert notes[0].name.startswith("2026-09-08 1205 - ")


def test_dry_run_does_not_report_files_it_would_have_moved(tmp_path):
    # Um dry-run que avisa "nao soube classificar" sobre tudo o que ele vai mover
    # e pior que nenhum: o usuario le o aviso e desiste de aplicar.
    vault = legacy_vault(tmp_path)
    import logging

    records = []
    handler = logging.Handler()
    handler.emit = records.append
    migrate.log.addHandler(handler)
    try:
        migrate.apply_migration(vault, dry_run=True)
    finally:
        migrate.log.removeHandler(handler)
    assert [r.getMessage() for r in records] == []


def test_the_orphan_pass_does_not_plan_a_book_epub_twice(tmp_path):
    vault = legacy_vault(tmp_path)
    moves = migrate.apply_migration(vault, dry_run=True)
    epubs = [m for m in moves if m.src.suffix == ".epub"]
    assert len(epubs) == 1


def test_an_epub_in_the_legacy_books_folder_resolves_chapters(tmp_path):
    # Caso real do vault: "rapido e devagar.epub" mora em Books/, nao na raiz, e
    # as notas dele sairiam sem capitulo nenhum.
    vault = legacy_vault(tmp_path)
    (vault / "livro.epub").unlink()
    make_epub_with(vault / "Books" / "livro.epub", [CH_ONE, CH_TWO])
    migrate.apply_migration(vault, dry_run=False)
    notes = list((vault / "Livros" / "livro" / "Anotações").glob("*.md"))
    assert notes[0].name == "2026-09-08 1205 - Crítica à tese.md"
    assert (vault / "Livros" / "livro" / "fonte" / "livro.epub").is_file()


def test_an_epub_with_no_notes_gets_its_own_book_directory(tmp_path):
    vault = legacy_vault(tmp_path)
    make_epub_with(vault / "Books" / "outro livro.epub", [CH_ONE])
    migrate.apply_migration(vault, dry_run=False)
    assert (vault / "Livros" / "outro livro" / "fonte" / "outro livro.epub").is_file()


def test_a_vault_already_split_by_book_gets_routed_by_kind(tmp_path):
    # O layout intermediario: um `Notas` por livro, antes das pastas por tipo.
    vault = tmp_path / "Reading"
    notas = vault / "Livros" / "livro" / "Notas"
    notas.mkdir(parents=True)
    (notas / "02-001324 Crítica à tese.md").write_text(
        NOTE.replace(
            "word_offset: 1324", "word_offset: 1324" + chr(10) + "kind: pergunta"
        ),
        encoding="utf-8",
    )
    fonte = vault / "Livros" / "livro" / "fonte"
    fonte.mkdir(parents=True)
    make_epub_with(fonte / "livro.epub", [CH_ONE, CH_TWO])

    migrate.apply_migration(vault, dry_run=False)
    assert (
        vault / "Livros" / "livro" / "Perguntas"
        / "2026-09-08 1205 - Crítica à tese.md"
    ).is_file()
    # O `Notas` vazio sai do caminho.
    assert not notas.exists()


def test_a_kind_already_in_the_frontmatter_is_trusted(tmp_path):
    # Reclassificar apagaria uma decisao que o pipeline ja tomou com o LLM.
    vault = legacy_vault(
        tmp_path,
        notes={
            "nota.md": NOTE.replace(
                "word_offset: 1324",
                "word_offset: 1324" + chr(10) + "kind: recall",
            )
        },
    )
    migrate.apply_migration(vault, dry_run=False)
    assert list((vault / "Livros" / "livro" / "Recall").glob("*.md"))


def test_migration_from_the_intermediate_layout_is_idempotent(tmp_path):
    vault = legacy_vault(tmp_path)
    migrate.apply_migration(vault, dry_run=False)
    snapshot = sorted(p.relative_to(vault).as_posix() for p in vault.rglob("*"))
    assert migrate.apply_migration(vault, dry_run=False) == []
    assert sorted(p.relative_to(vault).as_posix() for p in vault.rglob("*")) == snapshot


def test_migration_creates_every_kind_directory(tmp_path):
    # Uma pasta ausente e invisivel: abrir o livro e nao ver Recall deixa o recall
    # sem lugar obvio para cair.
    vault = legacy_vault(tmp_path)
    migrate.apply_migration(vault, dry_run=False)
    book = vault / "Livros" / "livro"
    assert (book / "Anotações").is_dir()
    assert (book / "Perguntas").is_dir()
    assert (book / "Recall").is_dir()


def test_a_missing_kind_directory_is_created_on_an_already_migrated_vault(tmp_path):
    # Um vault reorganizado antes de os tipos virarem pasta so tem as que precisou.
    vault = tmp_path / "Reading"
    (vault / "Livros" / "livro" / "Anotações").mkdir(parents=True)
    moves = migrate.apply_migration(vault, dry_run=False)
    assert moves
    assert (vault / "Livros" / "livro" / "Recall").is_dir()
    assert (vault / "Livros" / "livro" / "Perguntas").is_dir()
    # E na segunda vez nao ha nada a fazer.
    assert migrate.apply_migration(vault, dry_run=False) == []


def test_filling_kind_directories_respects_dry_run(tmp_path):
    vault = tmp_path / "Reading"
    (vault / "Livros" / "livro" / "Anotações").mkdir(parents=True)
    assert migrate.apply_migration(vault, dry_run=True)
    assert not (vault / "Livros" / "livro" / "Recall").exists()


def test_a_coded_name_already_in_a_kind_directory_is_renamed(tmp_path):
    # A nota chegou na pasta certa sob a convencao antiga; a passagem de rotas nao
    # a ve, porque ela olha so de onde as notas vem.
    vault = tmp_path / "Reading"
    folder = vault / "Livros" / "livro" / "Anotações"
    folder.mkdir(parents=True)
    (folder / "08-012438 Crítica à tese.md").write_text(NOTE, encoding="utf-8")

    migrate.apply_migration(vault, dry_run=False)
    assert [p.name for p in folder.glob("*.md")] == [
        "2026-09-08 1205 - Crítica à tese.md"
    ]
    # E na segunda vez o nome ja e o que ele quer, entao nada acontece.
    assert migrate.apply_migration(vault, dry_run=False) == []


def test_renaming_inside_a_kind_directory_relinks_the_board(tmp_path):
    vault = tmp_path / "Reading"
    book = vault / "Livros" / "livro"
    (book / "Anotações").mkdir(parents=True)
    (book / "Anotações" / "08-012438 Crítica à tese.md").write_text(
        NOTE, encoding="utf-8"
    )
    (book / "Quadro.md").write_text(
        BOARD.replace("2026-09-08 1205 - Crítica à tese", "08-012438 Crítica à tese"),
        encoding="utf-8",
    )
    migrate.apply_migration(vault, dry_run=False)
    board = (book / "Quadro.md").read_text(encoding="utf-8")
    assert "[[2026-09-08 1205 - Crítica à tese]]" in board
    assert "08-012438" not in board


def test_renaming_respects_dry_run(tmp_path):
    vault = tmp_path / "Reading"
    folder = vault / "Livros" / "livro" / "Anotações"
    folder.mkdir(parents=True)
    (folder / "08-012438 Crítica à tese.md").write_text(NOTE, encoding="utf-8")
    assert migrate.apply_migration(vault, dry_run=True)
    assert (folder / "08-012438 Crítica à tese.md").is_file()


def test_a_note_without_a_usable_date_keeps_its_name(tmp_path):
    vault = tmp_path / "Reading"
    folder = vault / "Livros" / "livro" / "Anotações"
    folder.mkdir(parents=True)
    (folder / "sem data.md").write_text(
        NOTE.replace("date: 2026-09-08T12:05:00", "date: sei la"), encoding="utf-8"
    )
    migrate.apply_migration(vault, dry_run=False)
    assert (folder / "sem data.md").is_file()
