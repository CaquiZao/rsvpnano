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
