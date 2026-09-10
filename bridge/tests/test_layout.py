from handy_bridge import layout


def test_note_stem_sorts_by_reading_position():
    a = layout.note_stem(4, 12438, "Crítica à tese", width=2)
    b = layout.note_stem(10, 300, "Outra coisa", width=2)
    assert a == "04-012438 Crítica à tese"
    assert b == "10-000300 Outra coisa"
    # Ordem de leitura, nao alfabetica nem cronologica.
    assert sorted([b, a]) == [a, b]


def test_note_stem_orders_within_a_chapter_by_offset():
    early = layout.note_stem(3, 1324, "Antes", width=2)
    late = layout.note_stem(3, 8120, "Depois", width=2)
    assert sorted([late, early]) == [early, late]


def test_note_stem_handles_a_missing_offset_and_chapter():
    assert layout.note_stem(4, None, "Sem offset", width=2) == "04-000000 Sem offset"
    assert layout.note_stem(None, None, "Solta", width=2) == "00-000000 Solta"


def test_note_stem_widens_for_a_long_book():
    assert layout.note_stem(7, 1, "T", width=3) == "007-000001 T"


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
    assert layout.notes_dir(v, "Sapiens") == v / "Livros" / "Sapiens" / "Notas"
    assert layout.chapters_dir(v, "Sapiens") == v / "Livros" / "Sapiens" / "Capítulos"
    assert layout.source_dir(v, "Sapiens") == v / "Livros" / "Sapiens" / "fonte"
    assert layout.board_path(v, "Sapiens") == v / "Livros" / "Sapiens" / "Quadro.md"
    assert layout.book_summary_path(v, "Sapiens") == v / "Livros" / "Sapiens" / "Sapiens.md"


def test_notes_without_a_book_go_to_geral(tmp_path):
    assert layout.notes_dir(tmp_path, None) == tmp_path / "Geral" / "Notas"
    assert layout.board_path(tmp_path, None) == tmp_path / "Geral" / "Quadro.md"
    # Geral nao fica dentro de Livros: nao e um livro.
    assert layout.BOOKS_DIR not in layout.notes_dir(tmp_path, None).parts


def test_a_book_literally_named_geral_still_lands_under_livros(tmp_path):
    got = layout.notes_dir(tmp_path, "Geral")
    assert got == tmp_path / "Livros" / "Geral" / "Notas"


def test_chapter_summary_path_is_numbered_and_titled(tmp_path):
    got = layout.chapter_summary_path(tmp_path, "Sapiens", 4, "Os Navegadores", width=2)
    assert got == tmp_path / "Livros" / "Sapiens" / "Capítulos" / "04 - Os Navegadores.md"


def test_chapter_summary_path_survives_a_title_with_reserved_characters(tmp_path):
    got = layout.chapter_summary_path(tmp_path, "S", 1, 'A/B: "C"?', width=2)
    assert "/" not in got.name.replace(".md", "")
    assert got.parent == tmp_path / "Livros" / "S" / "Capítulos"
