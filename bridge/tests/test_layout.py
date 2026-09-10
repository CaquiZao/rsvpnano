from datetime import datetime

from handy_bridge import layout


def test_note_stem_reads_as_a_date():
    got = layout.note_stem(datetime(2026, 9, 8, 12, 5, 0), "Crítica à tese")
    assert got == "2026-09-08 1205 - Crítica à tese"


def test_note_stem_sorts_chronologically():
    early = layout.note_stem(datetime(2026, 9, 8, 12, 5), "Antes")
    late = layout.note_stem(datetime(2026, 9, 9, 22, 5), "Depois")
    assert sorted([late, early]) == [early, late]


def test_note_stem_sanitises_the_title():
    got = layout.note_stem(datetime(2026, 9, 8, 12, 5), 'A/B: "C"?')
    assert "/" not in got and '"' not in got and ":" not in got


def test_note_stem_survives_an_empty_title():
    got = layout.note_stem(datetime(2026, 9, 8, 12, 5), "   ")
    assert got == "2026-09-08 1205 - Sem titulo"


def test_safe_stem_refuses_to_escape_the_vault():
    # O stem vem do device, entao nao pode virar caminho para fora do vault.
    assert layout.safe_stem("../../etc/passwd") == "passwd"
    assert layout.safe_stem("a/b/livro") == "livro"
    assert layout.safe_stem("a\\b\\livro") == "livro"
    # Vazio significa "sem livro", e nao o nome da pasta de notas soltas: um livro
    # chamado "Geral" nao pode colidir com elas.
    assert layout.safe_stem("") == ""
    assert layout.safe_stem("..") == ""
    assert layout.safe_stem(".") == ""
    assert layout.safe_stem(None) == ""


def test_book_paths_all_live_under_one_directory(tmp_path):
    v = tmp_path
    assert layout.book_dir(v, "Sapiens") == v / "Livros" / "Sapiens"
    assert layout.notes_dir(v, "Sapiens") == v / "Livros" / "Sapiens" / "Anotações"
    assert layout.notes_dir(v, "Sapiens", "pergunta") == (
        v / "Livros" / "Sapiens" / "Perguntas"
    )
    assert layout.notes_dir(v, "Sapiens", "recall") == (
        v / "Livros" / "Sapiens" / "Recall"
    )
    assert layout.chapters_dir(v, "Sapiens") == v / "Livros" / "Sapiens" / "Capítulos"
    assert layout.source_dir(v, "Sapiens") == v / "Livros" / "Sapiens" / "fonte"
    assert layout.board_path(v, "Sapiens") == v / "Livros" / "Sapiens" / "Quadro.md"
    assert layout.book_summary_path(v, "Sapiens") == v / "Livros" / "Sapiens" / "Sapiens.md"


def test_notes_without_a_book_go_to_geral(tmp_path):
    assert layout.notes_dir(tmp_path, None) == tmp_path / "Geral" / "Anotações"
    assert layout.board_path(tmp_path, None) == tmp_path / "Geral" / "Quadro.md"
    # Geral nao fica dentro de Livros: nao e um livro.
    assert layout.BOOKS_DIR not in layout.notes_dir(tmp_path, None).parts


def test_a_book_literally_named_geral_still_lands_under_livros(tmp_path):
    got = layout.notes_dir(tmp_path, "Geral")
    assert got == tmp_path / "Livros" / "Geral" / "Anotações"


def test_chapter_summary_path_is_numbered_and_titled(tmp_path):
    got = layout.chapter_summary_path(tmp_path, "Sapiens", 4, "Os Navegadores", width=2)
    assert got == tmp_path / "Livros" / "Sapiens" / "Capítulos" / "04 - Os Navegadores.md"


def test_chapter_summary_path_survives_a_title_with_reserved_characters(tmp_path):
    got = layout.chapter_summary_path(tmp_path, "S", 1, 'A/B: "C"?', width=2)
    assert "/" not in got.name.replace(".md", "")
    assert got.parent == tmp_path / "Livros" / "S" / "Capítulos"


def test_an_unknown_kind_falls_back_to_the_default_folder(tmp_path):
    # Um kind invalido nunca pode fazer a nota desaparecer numa pasta inventada.
    got = layout.notes_dir(tmp_path, "S", "reflexão")
    assert got == tmp_path / "Livros" / "S" / "Anotações"


def test_all_notes_dirs_covers_every_kind(tmp_path):
    got = layout.all_notes_dirs(tmp_path, "S")
    assert [p.name for p in got] == ["Anotações", "Perguntas", "Recall"]
