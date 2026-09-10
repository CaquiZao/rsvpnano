from handy_bridge import chapters as chapters_mod, summaries


def test_regeneration_preserves_my_own_observations(tmp_path):
    path = tmp_path / "04 - Cap.md"
    path.write_text(
        "---\ngenerated: true\n---\n\n## Síntese\n\nvelho\n\n"
        "## Minhas observações\n\nisso eu escrevi à mão\n",
        encoding="utf-8",
    )
    kept = summaries.read_observations(path)
    assert kept.strip() == "isso eu escrevi à mão"

    summaries.write_chapter_summary(
        path,
        summaries.render_chapter(
            chapter=4, title="Cap", synthesis="novo", sections={}, observations=kept
        ),
    )
    out = path.read_text(encoding="utf-8")
    assert "isso eu escrevi à mão" in out
    assert "novo" in out
    assert "velho" not in out


def test_observations_survive_headings_that_imitate_generated_ones(tmp_path):
    # E o caso que um parser ingenuo erra: a secao reservada e a ultima por
    # construcao, entao tudo depois dela e do usuario, cabecalho ou nao.
    path = tmp_path / "04 - Cap.md"
    path.write_text(
        "## Síntese\n\ns\n\n## Minhas observações\n\n"
        "## Anotações\n\ntexto meu sob um cabeçalho que parece gerado\n",
        encoding="utf-8",
    )
    kept = summaries.read_observations(path)
    assert "cabeçalho que parece gerado" in kept
    assert "## Anotações" in kept


def test_missing_file_yields_empty_observations(tmp_path):
    assert summaries.read_observations(tmp_path / "nao-existe.md") == ""


def test_a_file_without_the_reserved_section_yields_nothing(tmp_path):
    path = tmp_path / "c.md"
    path.write_text("## Síntese\n\napenas gerado\n", encoding="utf-8")
    assert summaries.read_observations(path) == ""


def test_the_synthesis_is_capped(tmp_path):
    long = " ".join(["palavra"] * 900)
    out = summaries.render_chapter(
        chapter=1, title="C", synthesis=long, sections={}, observations=""
    )
    synthesis = out.split("## ")[1]
    assert len(synthesis.split()) <= summaries.SYNTHESIS_WORD_CAP + 5


def test_the_chapter_summary_always_has_the_reserved_section(tmp_path):
    out = summaries.render_chapter(
        chapter=1, title="C", synthesis="s", sections={}, observations=""
    )
    assert summaries.OBSERVATIONS_HEADING in out
    # Ultima secao do arquivo, para que preservar seja ler ate o fim.
    assert out.rstrip().split("## ")[-1].startswith("Minhas observações")


def test_the_chapter_summary_is_marked_as_generated(tmp_path):
    out = summaries.render_chapter(
        chapter=1, title="C", synthesis="s", sections={}, observations=""
    )
    assert "generated: true" in out


def test_sections_render_in_a_fixed_order(tmp_path):
    out = summaries.render_chapter(
        chapter=1,
        title="C",
        synthesis="s",
        sections={
            "Recall": ["eu disse X / na verdade Y"],
            "Anotações": ["achei interessante"],
            "Perguntas e respostas": ["o que é X? — é Y"],
        },
        observations="",
    )
    assert out.index("## Anotações") < out.index("## Perguntas e respostas")
    assert out.index("## Perguntas e respostas") < out.index("## Recall")


def test_an_empty_section_is_omitted(tmp_path):
    out = summaries.render_chapter(
        chapter=1, title="C", synthesis="s", sections={"Recall": []}, observations=""
    )
    assert "## Recall" not in out


def test_write_chapter_summary_is_atomic(tmp_path):
    path = tmp_path / "c.md"
    summaries.write_chapter_summary(path, "conteudo\n")
    assert [p.name for p in tmp_path.iterdir()] == ["c.md"]


# --- resumo do livro --------------------------------------------------------

IDX = chapters_mod.BookIndex(
    chapters=[
        chapters_mod.Chapter(1, "Um", 10, "um"),
        chapters_mod.Chapter(2, "Dois", 10, "dois"),
        chapters_mod.Chapter(3, "Três", 10, "tres"),
    ]
)


def test_book_summary_is_built_from_syntheses_not_raw_notes(tmp_path):
    ch = tmp_path / "Capítulos"
    ch.mkdir()
    (ch / "01 - Um.md").write_text(
        "## Síntese\n\nsintese do um\n\n## Anotações\n\nnota crua que nao deve entrar\n",
        encoding="utf-8",
    )
    got = summaries.collect_syntheses(ch)
    assert got == [(1, "Um", "sintese do um")]
    assert "nota crua" not in got[0][2]


def test_syntheses_come_back_in_chapter_order(tmp_path):
    ch = tmp_path / "Capítulos"
    ch.mkdir()
    for number, title in ((10, "Dez"), (2, "Dois")):
        (ch / f"{number:02d} - {title}.md").write_text(
            f"## Síntese\n\ns{number}\n", encoding="utf-8"
        )
    assert [n for n, _, _ in summaries.collect_syntheses(ch)] == [2, 10]


def test_unrecorded_chapters_are_listed(tmp_path):
    ch = tmp_path / "Capítulos"
    ch.mkdir()
    (ch / "02 - Dois.md").write_text("## Síntese\n\ns\n", encoding="utf-8")
    missing = summaries.unrecorded_chapters(IDX, ch)
    assert [n for n, _ in missing] == [1, 3]


def test_the_book_summary_lists_what_went_unrecorded(tmp_path):
    out = summaries.render_book(
        title="Sapiens",
        bullets=["um ponto importante"],
        syntheses=[(1, "Um", "sintese")],
        unrecorded=[(2, "Dois")],
    )
    assert "um ponto importante" in out
    assert "Dois" in out
    assert "generated: true" in out


def test_collect_syntheses_ignores_a_file_without_the_heading(tmp_path):
    ch = tmp_path / "Capítulos"
    ch.mkdir()
    (ch / "01 - Um.md").write_text("sem cabeçalho nenhum\n", encoding="utf-8")
    assert summaries.collect_syntheses(ch) == []


def test_collect_syntheses_on_a_missing_directory_is_empty(tmp_path):
    assert summaries.collect_syntheses(tmp_path / "nao-existe") == []


# --- leitura das notas de volta ---------------------------------------------

NOTE_TEXT = """---
title: "Física e química"
kind: pergunta
chapter: 3
---

Estou lendo Sapiens e fiquei na dúvida sobre a ordem das ciências.

> [!quote] Trecho que eu estava lendo
> humanos chegaram a Flores

> [!question] Qual a definição de física?
> Estuda matéria, energia e forças.

> [!note]- Transcrição original
> kind: isso aqui nao e frontmatter
"""

RECALL_TEXT = """---
title: "Recapitulando"
kind: recall
chapter: 3
---

Falei o que entendi.

> [!success] Conferência do que você lembrou
>
> ✓ **Você disse:** A física veio antes
>
> ✗ **Você disse:** Átomos no primeiro segundo
> **Na verdade:** Alguns minutos depois
>
> — **Passou batido:** O trecho falava de Flores
>
> *Conferência automática, não verificada.*
"""


def test_parse_note_reads_frontmatter_body_and_questions(tmp_path):
    path = tmp_path / "03-000010 Física e química.md"
    path.write_text(NOTE_TEXT, encoding="utf-8")
    got = summaries.parse_note(path)
    assert got.kind == "pergunta"
    assert got.title == "Física e química"
    assert "ordem das ciências" in got.body
    # O trecho do livro nao entra no corpo: o resumo nao usa o texto do livro.
    assert "Flores" not in got.body
    assert got.questions == [
        ("Qual a definição de física?", "Estuda matéria, energia e forças.")
    ]


def test_parse_note_ignores_a_field_written_inside_the_body(tmp_path):
    # A transcricao original contem "kind:" e nao pode virar a propriedade da nota.
    path = tmp_path / "03-000010 x.md"
    path.write_text(NOTE_TEXT, encoding="utf-8")
    assert summaries.parse_note(path).kind == "pergunta"


def test_parse_note_reads_recall_points_side_by_side(tmp_path):
    path = tmp_path / "03-000020 Recapitulando.md"
    path.write_text(RECALL_TEXT, encoding="utf-8")
    got = summaries.parse_note(path)
    assert got.kind == "recall"
    assert len(got.recall) == 3
    assert "Na verdade:** Alguns minutos depois" in got.recall[1]
    assert got.recall[2].startswith("—")


def test_collect_chapter_notes_takes_only_that_chapter(tmp_path):
    notes = tmp_path / "Notas"
    notes.mkdir()
    (notes / "03-000010 a.md").write_text(NOTE_TEXT, encoding="utf-8")
    (notes / "03-000020 b.md").write_text(RECALL_TEXT, encoding="utf-8")
    (notes / "08-000030 c.md").write_text(NOTE_TEXT, encoding="utf-8")
    got = summaries.collect_chapter_notes(notes, 3)
    assert [n.stem[:2] for n in got] == ["03", "03"]


def test_collect_chapter_notes_handles_a_three_digit_prefix(tmp_path):
    notes = tmp_path / "Notas"
    notes.mkdir()
    (notes / "003-000010 a.md").write_text(NOTE_TEXT, encoding="utf-8")
    assert len(summaries.collect_chapter_notes(notes, 3)) == 1


def test_sections_from_splits_by_kind_and_links_back(tmp_path):
    notes = tmp_path / "Notas"
    notes.mkdir()
    (notes / "03-000010 a.md").write_text(
        NOTE_TEXT.replace("kind: pergunta", "kind: anotação"), encoding="utf-8"
    )
    (notes / "03-000020 b.md").write_text(RECALL_TEXT, encoding="utf-8")
    got = summaries.sections_from(summaries.collect_chapter_notes(notes, 3))
    assert any("[[03-000010 a]]" in item for item in got["Anotações"])
    assert any("definição de física" in item for item in got["Perguntas e respostas"])
    assert any("[[03-000020 b]]" in item for item in got["Recall"])


def test_synthesis_input_carries_the_kind_of_each_line(tmp_path):
    notes = tmp_path / "Notas"
    notes.mkdir()
    (notes / "03-000020 b.md").write_text(RECALL_TEXT, encoding="utf-8")
    got = summaries.synthesis_input(summaries.collect_chapter_notes(notes, 3))
    assert any(line.startswith("[recall]") for line in got)


def test_a_recall_note_with_no_check_still_shows_up(tmp_path):
    notes = tmp_path / "Notas"
    notes.mkdir()
    bare = RECALL_TEXT.split("> [!success]")[0]
    (notes / "03-000020 b.md").write_text(bare, encoding="utf-8")
    got = summaries.sections_from(summaries.collect_chapter_notes(notes, 3))
    assert got["Recall"]


def test_parse_note_on_a_missing_file_is_none(tmp_path):
    assert summaries.parse_note(tmp_path / "nada.md") is None
