from handy_bridge import chapters as chapters_mod
from test_epub import make_epub_with

# A passagem repetida e longa de proposito: um trecho real vem do device como um
# paragrafo inteiro, e MIN_EXCERPT_CHARS existe justamente para descartar trecho
# curto demais para identificar posicao.
REPEATED = "o gato subiu no telhado e ficou olhando a rua inteira sem pressa"
UNIQUE = "humanos chegaram a ilha de Flores quando o nivel do mar estava baixo"

IDX = chapters_mod.BookIndex(
    chapters=[
        chapters_mod.Chapter(1, "Um", 100, chapters_mod.normalize(REPEATED)),
        chapters_mod.Chapter(2, "Dois", 100, chapters_mod.normalize(UNIQUE)),
        chapters_mod.Chapter(3, "Três", 100, chapters_mod.normalize(REPEATED)),
    ]
)


def test_resolve_exact_match_is_exact():
    got = chapters_mod.resolve(IDX, UNIQUE + "!", word_offset=None)
    assert (got.chapter, got.source) == (2, "exato")


def test_resolve_ignores_punctuation_case_and_spacing():
    # E a diferenca real entre o excerpt do device e o markdown do bridge.
    messy = "  HUMANOS   chegaram à ilha de Flores,\n quando o nível do mar estava baixo  "
    got = chapters_mod.resolve(IDX, messy, word_offset=None)
    assert got.chapter == 2


def test_resolve_ambiguous_uses_word_offset_to_break_the_tie():
    # Capitulos 1 e 3 tem o mesmo texto. Offset 250 esta mais perto do inicio do 3.
    got = chapters_mod.resolve(IDX, REPEATED, word_offset=250)
    assert (got.chapter, got.source) == (3, "exato")


def test_resolve_ambiguous_without_offset_takes_the_first():
    got = chapters_mod.resolve(IDX, REPEATED, word_offset=None)
    assert (got.chapter, got.source) == (1, "exato")


def test_resolve_without_match_estimates_from_offset():
    got = chapters_mod.resolve(IDX, "texto que nao existe em lugar nenhum do livro", 150)
    assert (got.chapter, got.source) == (2, "estimado")


def test_resolve_beyond_the_end_estimates_the_last_chapter():
    got = chapters_mod.resolve(IDX, None, word_offset=999999)
    assert (got.chapter, got.source) == (3, "estimado")


def test_resolve_without_excerpt_or_offset_gives_up():
    assert chapters_mod.resolve(IDX, None, None) is None


def test_resolve_prefers_exact_match_over_offset_disagreement():
    # O trecho manda: offset aproximado nao derruba um casamento inequivoco.
    got = chapters_mod.resolve(IDX, UNIQUE, word_offset=9999)
    assert (got.chapter, got.source) == (2, "exato")


def test_a_word_join_artefact_from_the_device_still_matches():
    # Regressao vinda do vault real: a nota de 2026-09-09 22:05 traz
    # "passaram porum processo" onde o livro diz "por um", e o casamento exato
    # morria nisso. E a conversao HTML-para-texto do device, nao um erro de ASR.
    book = "humanos arcaicos passaram por um processo que levou ao nanismo"
    index = chapters_mod.BookIndex(
        chapters=[
            chapters_mod.Chapter(1, "Outro", 50, chapters_mod.normalize("nada a ver")),
            chapters_mod.Chapter(2, "Certo", 50, chapters_mod.normalize(book)),
        ]
    )
    got = chapters_mod.resolve(index, "arcaicos passaram porum processo que levou", None)
    assert (got.chapter, got.source) == (2, "exato")


def test_the_strict_match_wins_when_the_text_is_clean():
    # Sem espaco, "aula" casaria dentro de "aulas"; com texto limpo o estrito manda.
    index = chapters_mod.BookIndex(
        chapters=[
            chapters_mod.Chapter(1, "Um", 50, chapters_mod.normalize(UNIQUE)),
            chapters_mod.Chapter(2, "Dois", 50, chapters_mod.normalize(UNIQUE + " extra")),
        ]
    )
    got = chapters_mod.resolve(index, UNIQUE + " extra", None)
    assert (got.chapter, got.source) == (2, "exato")


def test_a_short_excerpt_is_not_trusted():
    # "o gato" casaria em dois capitulos e nao identifica posicao nenhuma.
    got = chapters_mod.resolve(IDX, "o gato", word_offset=150)
    assert got.source == "estimado"


def test_width_pads_enough_for_the_chapter_count():
    assert IDX.width == 2
    wide = chapters_mod.BookIndex(
        chapters=[chapters_mod.Chapter(n, str(n), 1, "x") for n in range(1, 120)]
    )
    assert wide.width == 3


def test_start_of_sums_the_preceding_chapters():
    assert IDX.start_of(1) == 0
    assert IDX.start_of(3) == 200


def test_build_index_reads_titles_and_counts_words(tmp_path):
    epub = make_epub_with(
        tmp_path / "b.epub",
        [
            ("c1.xhtml", "<h1>Um</h1><p>tres palavras aqui</p>"),
            ("c2.xhtml", "<h1>Dois</h1><p>duas palavras</p>"),
        ],
    )
    index = chapters_mod.build_index(epub)
    assert [c.title for c in index.chapters] == ["Um", "Dois"]
    assert [c.word_count for c in index.chapters] == [3, 2]
    assert index.word_count == 5


def test_load_index_caches_and_survives_a_corrupt_cache(tmp_path):
    epub = make_epub_with(
        tmp_path / "livro.epub", [("c1.xhtml", "<h1>Um</h1><p>texto do capitulo um</p>")]
    )
    src = tmp_path / "fonte"
    first = chapters_mod.load_index(src, "livro", epub)
    assert first is not None
    assert chapters_mod.index_cache_path(src, "livro").is_file()

    # Um cache corrompido reconstroi em vez de explodir.
    chapters_mod.index_cache_path(src, "livro").write_text("{lixo", encoding="utf-8")
    again = chapters_mod.load_index(src, "livro", epub)
    assert again is not None
    assert again.chapters[0].title == "Um"


def test_load_index_returns_none_without_an_epub(tmp_path):
    assert chapters_mod.load_index(tmp_path, "sumido", tmp_path / "sumido.epub") is None


def test_load_index_returns_none_for_a_broken_epub(tmp_path):
    bad = tmp_path / "livro.epub"
    bad.write_bytes(b"nao sou um zip")
    # Regra global: conveniencia quebrada nunca pode virar excecao no pipeline.
    assert chapters_mod.load_index(tmp_path / "fonte", "livro", bad) is None


# --- janela de passagem para a correcao de recall ---------------------------

WORDS = " ".join(f"w{n}" for n in range(1, 101))
PASSAGE_IDX = chapters_mod.BookIndex(
    chapters=[
        chapters_mod.Chapter(1, "Um", 100, WORDS),
        chapters_mod.Chapter(2, "Dois", 100, WORDS),
        chapters_mod.Chapter(3, "Três", 100, WORDS),
    ]
)


def test_passage_never_starts_before_the_chapter():
    # Muita leitura sem gravar nao pode fazer a janela varrer o livro inteiro.
    got = chapters_mod.passage(PASSAGE_IDX, chapter=3, from_offset=0, to_offset=99999)
    assert got == WORDS


def test_passage_starts_at_the_last_recall_when_it_is_inside_the_chapter():
    # Capitulo 2 comeca na palavra 100; pedir de 150 a 160 pega 10 palavras.
    got = chapters_mod.passage(PASSAGE_IDX, chapter=2, from_offset=150, to_offset=160)
    assert got.split() == [f"w{n}" for n in range(51, 61)]


def test_passage_clamps_the_end_to_the_chapter():
    got = chapters_mod.passage(PASSAGE_IDX, chapter=1, from_offset=0, to_offset=100000)
    assert got == WORDS


def test_passage_with_no_progress_gives_the_whole_chapter():
    # Primeiro recall do livro, ou offsets iguais: comparar contra nada nao serve.
    got = chapters_mod.passage(PASSAGE_IDX, chapter=2, from_offset=None, to_offset=None)
    assert got == WORDS


def test_passage_with_an_inverted_window_gives_the_whole_chapter():
    # Reler para tras deixaria a janela negativa; degrada em vez de devolver vazio.
    got = chapters_mod.passage(PASSAGE_IDX, chapter=2, from_offset=180, to_offset=120)
    assert got == WORDS


def test_passage_ignores_an_offset_from_a_different_scale():
    # Medido no vault real: as contagens do device e do bridge divergem por quase
    # o dobro. Um offset fora da faixa do capitulo significa que as escalas nao
    # concordam, e recortar produziria uma janela plausivel que corta texto lido.
    got = chapters_mod.passage(PASSAGE_IDX, chapter=2, from_offset=99999, to_offset=100050)
    assert got == WORDS


def test_passage_still_slices_when_the_offset_fits_the_chapter():
    # Capitulo 2 ocupa 100..200 na contagem do bridge, entao 150 e plausivel.
    got = chapters_mod.passage(PASSAGE_IDX, chapter=2, from_offset=150, to_offset=160)
    assert len(got.split()) == 10


def test_passage_for_an_unknown_chapter_is_empty():
    assert chapters_mod.passage(PASSAGE_IDX, chapter=99, from_offset=0, to_offset=1) == ""


def test_passage_uses_the_original_text_not_the_folded_one(tmp_path):
    # Acentos e pontuacao importam quando a passagem vai para o modelo comparar.
    epub = make_epub_with(
        tmp_path / "livro.epub",
        [("c1.xhtml", "<h1>Um</h1><p>A Revolução Agrícola foi a maior fraude.</p>")],
    )
    index = chapters_mod.build_index(epub)
    got = chapters_mod.passage(index, 1, None, None)
    assert "Revolução Agrícola" in got
    assert "revolucao" not in got


def test_the_cache_round_trips_the_original_text(tmp_path):
    epub = make_epub_with(
        tmp_path / "livro.epub",
        [("c1.xhtml", "<h1>Um</h1><p>A Revolução Agrícola foi a maior fraude.</p>")],
    )
    src = tmp_path / "fonte"
    chapters_mod.load_index(src, "livro", epub)
    from_cache = chapters_mod.load_index(src, "livro", epub)
    assert "Revolução Agrícola" in from_cache.chapters[0].text
    # A forma normalizada e recomputada na carga, nao guardada no disco.
    assert from_cache.chapters[0].normalized == chapters_mod.normalize(
        from_cache.chapters[0].text
    )
    assert "text" in chapters_mod.index_cache_path(src, "livro").read_text(
        encoding="utf-8"
    )
