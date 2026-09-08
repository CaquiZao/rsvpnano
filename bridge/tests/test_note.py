import pytest

from datetime import datetime

from handy_bridge.note import NoteData, render, slugify, write_note


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


def test_write_note_creates_inbox_and_names_file(tmp_path):
    path = write_note(tmp_path / "Inbox", sample())
    assert path.parent.is_dir()
    assert path.name == "2026-09-07 1432 - Ideia de captura por voz.md"
    assert "Texto limpo." in path.read_text(encoding="utf-8")


def test_write_note_never_overwrites(tmp_path):
    inbox = tmp_path / "Inbox"
    first = write_note(inbox, sample())
    second = write_note(inbox, sample())
    assert first != second
    assert second.name.endswith("-2.md")


def test_write_note_leaves_no_temp_files(tmp_path):
    inbox = tmp_path / "Inbox"
    write_note(inbox, sample())
    assert [p.name for p in inbox.iterdir() if p.suffix != ".md"] == []


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
    path = write_note(tmp_path / "Inbox", sample(answers=[("q1", "a1")]))
    append_followup(path, "e isso?", "assim.")
    text = path.read_text(encoding="utf-8")

    assert "> [!question] e isso?" in text
    assert "> assim." in text
    # Perguntas ficam juntas, acima da transcricao crua
    assert text.index("[!question] e isso?") < text.index("Transcrição original")


def test_append_followup_appends_at_the_end_when_there_is_no_transcript(tmp_path):
    path = write_note(tmp_path / "Inbox", sample(raw_transcript=""))
    append_followup(path, "q", "a")
    assert path.read_text(encoding="utf-8").rstrip().endswith("> a")


def test_several_followups_keep_their_order(tmp_path):
    path = write_note(tmp_path / "Inbox", sample())
    append_followup(path, "primeira", "r1")
    append_followup(path, "segunda", "r2")
    text = path.read_text(encoding="utf-8")
    assert text.index("primeira") < text.index("segunda")


def test_append_followup_quotes_a_multiline_answer(tmp_path):
    path = write_note(tmp_path / "Inbox", sample())
    append_followup(path, "q", "linha um\nlinha dois")
    assert "> linha um\n> linha dois" in path.read_text(encoding="utf-8")


def test_append_followup_leaves_no_temp_file(tmp_path):
    inbox = tmp_path / "Inbox"
    path = write_note(inbox, sample())
    append_followup(path, "q", "a")
    assert [p.name for p in inbox.iterdir() if p.suffix != ".md"] == []


def test_append_followup_on_a_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        append_followup(tmp_path / "nao-existe.md", "q", "a")
