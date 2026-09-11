import pytest

from datetime import datetime

from pathlib import Path

from handy_bridge import note as note_mod
from handy_bridge.note import (
    NoteData,
    render,
    slugify,
    write_note,
)


def sample(**over) -> NoteData:
    base = dict(
        title="Ideia de captura por voz",
        tags=["ideia", "projeto/rsvpnano"],
        body="Texto limpo.",
        raw_transcript="entao eh tipo assim texto cru",
        recorded_at=datetime(2026, 9, 7, 14, 32, 11),
        date_estimated=False,
        duration_s=47.0,
        asr_model="nemotron-3.5-asr-streaming-0.6b",
        book=None,
        word_offset=None,
        excerpt=None,
        answers=[],
    )
    base.update(over)
    return NoteData(**base)


def test_render_has_frontmatter_and_raw_callout():
    out = render(sample())
    assert out.startswith("---\n")
    assert 'title: "Ideia de captura por voz"' in out
    assert "date: 2026-09-07T14:32:11" in out
    assert "duration: 47s" in out
    assert "source: rsvp-nano" in out
    assert "tags: [ideia, projeto/rsvpnano]" in out
    assert "Texto limpo." in out
    assert "> [!note]- Transcrição original" in out
    assert "> entao eh tipo assim texto cru" in out
    assert "date_estimated" not in out


def test_render_flags_estimated_date():
    assert "date_estimated: true" in render(sample(date_estimated=True))


def test_render_escapes_quotes_in_title():
    assert 'title: "Ele disse \\"oi\\""' in render(sample(title='Ele disse "oi"'))


def test_multiline_raw_transcript_is_fully_quoted():
    out = render(sample(raw_transcript="linha um\nlinha dois"))
    assert "> linha um\n> linha dois" in out


def test_render_includes_book_anchor_when_present():
    out = render(
        sample(
            book="epdf.pub_sapiens",
            word_offset=12438,
            excerpt="a Revolução Agrícola foi a maior fraude da história",
        )
    )
    assert 'book: "[[epdf.pub_sapiens]]"' in out
    assert "word_offset: 12438" in out
    assert "> [!quote] Trecho que eu estava lendo" in out
    assert "> a Revolução Agrícola foi a maior fraude da história" in out


def test_render_omits_book_anchor_for_standalone_note():
    out = render(sample())
    assert "book:" not in out
    assert "word_offset:" not in out
    assert "[!quote]" not in out


def test_render_allows_word_offset_zero():
    # offset 0 is the first word of the book, not "absent"
    assert "word_offset: 0" in render(sample(book="b", word_offset=0))


def test_render_quotes_excerpt_even_without_book():
    out = render(sample(excerpt="linha um\nlinha dois"))
    assert "> linha um\n> linha dois" in out
    assert "book:" not in out


def test_tags_with_spaces_become_hyphenated():
    # Obsidian tags accept letters, digits, _, - and / — never spaces.
    out = render(sample(tags=["revolução agrícola", "ganho demográfico"]))
    assert "tags: [revolução-agrícola, ganho-demográfico]" in out


def test_tags_are_lowercased_and_stripped_of_hash():
    out = render(sample(tags=["#História", "  Debate  "]))
    assert "tags: [história, debate]" in out


def test_tags_keep_nested_slashes():
    assert "tags: [leitura/sapiens]" in render(sample(tags=["leitura/sapiens"]))


def test_empty_tags_are_dropped():
    out = render(sample(tags=["ideia", "", "  ", "#"]))
    assert "tags: [ideia]" in out


def test_slugify_replaces_reserved_path_chars_and_keeps_accents():
    assert slugify("Ideia: captura/voz?") == "Ideia captura-voz"
    assert slugify("Gravação de áudio") == "Gravação de áudio"
    assert slugify("   ") == "Sem titulo"


def test_write_note_creates_the_folder_and_names_by_date(tmp_path):
    path = write_note(tmp_path / "Anotações", sample(chapter=1, word_offset=1324))
    assert path.parent.is_dir()
    assert path.name == "2026-09-07 1432 - Ideia de captura por voz.md"
    assert "Texto limpo." in path.read_text(encoding="utf-8")
    # A posicao de leitura segue no frontmatter, que e de onde os resumos a leem.
    assert "chapter: 1" in path.read_text(encoding="utf-8")


def test_write_note_never_overwrites(tmp_path):
    notes = tmp_path / "Anotações"
    first = write_note(notes, sample())
    second = write_note(notes, sample())
    assert first != second
    assert second.name.endswith("-2.md")


def test_write_note_leaves_no_temp_files(tmp_path):
    notes = tmp_path / "Anotações"
    write_note(notes, sample())
    assert [p.name for p in notes.iterdir() if p.suffix != ".md"] == []


# --- kind e capitulo --------------------------------------------------------


def test_frontmatter_carries_kind_and_chapter():
    out = render(
        sample(
            kind="recall",
            book="Sapiens",
            word_offset=12438,
            chapter=8,
            chapter_title="A maior fraude da história",
            chapter_source="exato",
        )
    )
    assert "kind: recall" in out
    assert "chapter: 8" in out
    assert 'chapter_title: "A maior fraude da história"' in out
    assert "chapter_source: exato" in out


def test_kind_is_always_written_even_for_a_standalone_note():
    # E o eixo que as views de Bases filtram: sem ele a nota fica invisivel nas tres.
    out = render(sample(book=None, chapter=None))
    assert "kind: anotação" in out


def test_chapter_fields_are_omitted_when_there_is_no_chapter():
    out = render(sample(book=None, chapter=None))
    assert "chapter:" not in out
    assert "chapter_title:" not in out
    assert "chapter_source:" not in out


def test_chapter_zero_is_still_written():
    # Capitulo 0 nao existe hoje, mas comparar com None e nao com falsy evita que
    # um indice base-zero futuro desapareca em silencio.
    out = render(sample(book="S", chapter=0, chapter_title="Capa"))
    assert "chapter: 0" in out


def test_render_includes_answered_questions():
    out = render(
        sample(
            answers=[
                ("O que foi o Big Bang?", "O evento inicial do universo."),
                ("Quem foi Harari?", "O autor do livro."),
            ]
        )
    )
    assert "> [!question] O que foi o Big Bang?" in out
    assert "> O evento inicial do universo." in out
    assert "> [!question] Quem foi Harari?" in out
    # A ordem das perguntas e preservada
    assert out.index("Big Bang") < out.index("Harari")


def test_render_without_answers_has_no_question_callout():
    assert "[!question]" not in render(sample())


def test_multiline_answer_is_fully_quoted():
    out = render(sample(answers=[("q", "linha um\nlinha dois")]))
    assert "> linha um\n> linha dois" in out


def test_answers_come_before_the_raw_transcript():
    out = render(sample(answers=[("q", "a")]))
    assert out.index("[!question]") < out.index("Transcrição original")


# --- acompanhamento no Telegram ------------------------------------------

from handy_bridge.note import append_followup  # noqa: E402


def test_append_followup_inserts_before_the_raw_transcript(tmp_path):
    path = write_note(tmp_path / "Anotações", sample(answers=[("q1", "a1")]))
    append_followup(path, "e isso?", "assim.")
    text = path.read_text(encoding="utf-8")

    assert "> [!question] e isso?" in text
    assert "> assim." in text
    # Perguntas ficam juntas, acima da transcricao crua
    assert text.index("[!question] e isso?") < text.index("Transcrição original")


def test_append_followup_appends_at_the_end_when_there_is_no_transcript(tmp_path):
    path = write_note(tmp_path / "Anotações", sample(raw_transcript=""))
    append_followup(path, "q", "a")
    assert path.read_text(encoding="utf-8").rstrip().endswith("> a")


def test_several_followups_keep_their_order(tmp_path):
    path = write_note(tmp_path / "Anotações", sample())
    append_followup(path, "primeira", "r1")
    append_followup(path, "segunda", "r2")
    text = path.read_text(encoding="utf-8")
    assert text.index("primeira") < text.index("segunda")


def test_append_followup_quotes_a_multiline_answer(tmp_path):
    path = write_note(tmp_path / "Anotações", sample())
    append_followup(path, "q", "linha um\nlinha dois")
    assert "> linha um\n> linha dois" in path.read_text(encoding="utf-8")


def test_append_followup_leaves_no_temp_file(tmp_path):
    inbox = tmp_path / "Anotações"
    path = write_note(inbox, sample())
    append_followup(path, "q", "a")
    assert [p.name for p in inbox.iterdir() if p.suffix != ".md"] == []


def test_append_followup_on_a_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        append_followup(tmp_path / "nao-existe.md", "q", "a")


# --- conferencia de recall --------------------------------------------------


def test_recall_shows_what_was_said_beside_what_the_book_says():
    out = render(
        sample(
            kind="recall",
            recall_points=[
                ("A física veio antes da química", "", True),
                ("Átomos surgiram no primeiro segundo", "Surgiram alguns minutos depois", False),
            ],
        )
    )
    assert note_mod.RECALL_CALLOUT in out
    # Lado a lado, com peso igual: o erro nao fica escondido.
    assert "> ✓ **Você disse:** A física veio antes da química" in out
    assert "> ✗ **Você disse:** Átomos surgiram no primeiro segundo" in out
    assert "> **Na verdade:** Surgiram alguns minutos depois" in out


def test_a_correct_point_has_no_actual_line():
    out = render(sample(recall_points=[("Certo", "", True)]))
    assert "Na verdade" not in out


def test_missed_items_are_listed():
    out = render(sample(recall_missed=["O trecho falava de Flores", "E de Java"]))
    assert "> — **Passou batido:** O trecho falava de Flores" in out
    assert "> — **Passou batido:** E de Java" in out


def test_a_recall_correction_carries_the_unverified_warning():
    out = render(sample(recall_points=[("X", "Y", False)]))
    assert note_mod.RECALL_WARNING in out


def test_without_a_recall_there_is_no_callout():
    out = render(sample())
    assert note_mod.RECALL_CALLOUT not in out
    assert note_mod.RECALL_WARNING not in out


def test_the_recall_block_comes_before_the_raw_transcript():
    out = render(sample(recall_points=[("X", "", True)], answers=[("q", "a")]))
    assert out.index(note_mod.RECALL_CALLOUT) < out.index("[!question]")
    assert out.index("[!question]") < out.index(note_mod.RAW_CALLOUT)


def test_a_blank_point_is_dropped_without_breaking_the_callout():
    out = render(sample(recall_points=[("  ", "", True), ("Real", "", True)]))
    assert "Real" in out
    assert out.count("**Você disse:**") == 1


def test_render_recall_shows_the_reasoning_and_the_deepening_apart_from_the_check():
    lines = note_mod.render_recall(
        [("A veio antes de B", "", True)],
        [],
        reasoning="Seu instinto acertou a aceleracao.",
        deepening="A Belle Epoque foi o apice, nao a largada.",
    )
    text = "\n".join(lines)
    assert note_mod.RECALL_CALLOUT in text
    assert note_mod.REASONING_CALLOUT in text
    assert note_mod.DEEPENING_CALLOUT in text
    # A conferencia julga contra o texto; as outras duas usam conhecimento de fora,
    # e o aviso mais forte e o que separa uma coisa da outra para quem le.
    assert note_mod.RECALL_WARNING in text
    assert text.count(note_mod.DISCUSSION_WARNING) == 2


def test_render_recall_outside_the_passage_drops_the_check_and_says_why():
    lines = note_mod.render_recall(
        [], [], reasoning="Voce confundiu o inicio com o apice.", deepening="Mais contexto.",
        outside=True,
    )
    text = "\n".join(lines)
    assert note_mod.OUTSIDE_NOTE in text
    # Sem conferencia falsa: nao ha contra o que conferir.
    assert note_mod.RECALL_CALLOUT not in text
    assert note_mod.REASONING_CALLOUT in text


def test_render_recall_stays_empty_when_there_is_nothing_at_all():
    assert note_mod.render_recall([], [], "", "", False) == []
