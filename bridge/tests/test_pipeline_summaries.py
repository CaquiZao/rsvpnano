"""Phase 2 wiring: the recall check and the two derived summaries."""

from handy_bridge.config import Config, SummariesConfig
from handy_bridge.pipeline import IncomingNote, process_note
from handy_bridge.postprocess import (
    PostProcessError,
    PostProcessResult,
    RecallCheck,
    RecallPoint,
)
from test_pipeline import (
    CH_ONE,
    CH_TWO,
    make_cfg,
    make_wav,
    ok_transcribe,
    vault_with_epub,
)

CH_THREE = (
    "c3.xhtml",
    "<h1>Três</h1><p>um terceiro capitulo que ninguem gravou nada sobre</p>",
)


class SummarizingProcessor:
    """Double that records what each kind of call was asked for."""

    def __init__(self, *, kind="recall", recall=None, fail=None):
        self._kind = kind
        self._recall = recall or RecallCheck(
            points=[
                RecallPoint("A física veio antes", "", True),
                RecallPoint("Átomos no primeiro segundo", "Alguns minutos depois", False),
            ],
            missed=["O trecho falava de Flores"],
        )
        self._fail = fail
        self.recall_calls = []
        self.chapter_calls = []
        self.book_calls = []

    def process(self, transcript):
        return PostProcessResult(
            title="Meu titulo", tags=[], cleaned="Falei o que entendi.", kind=self._kind
        )

    def answer_tasks(self, questions, excerpt):
        return []

    def check_recall(self, spoken, passage):
        self.recall_calls.append((spoken, passage))
        return self._recall

    def summarize_chapter(self, entries):
        if self._fail == "chapter":
            raise PostProcessError("boom")
        self.chapter_calls.append(entries)
        return "Sintese gerada do capitulo."

    def summarize_book(self, syntheses):
        self.book_calls.append(syntheses)
        return ["Um ponto importante do livro"]


def with_summaries(cfg: Config, **over) -> Config:
    return Config(
        vault_path=cfg.vault_path,
        inbox_folder=cfg.inbox_folder,
        audio_store=cfg.audio_store,
        port=cfg.port,
        asr=cfg.asr,
        post_process=cfg.post_process,
        summaries=SummariesConfig(**over),
    )


def recall_note(tmp_path, name="r", offset=12):
    return IncomingNote(
        name,
        make_wav(tmp_path / f"{name}.wav"),
        {
            "clock_synced": True,
            "recorded_at": "2026-09-09T22:05:40",
            "book": "livro",
            "word_offset": offset,
            "excerpt": "humanos chegaram a ilha de Flores quando o nivel do mar",
        },
    )


def first_chapter_note(tmp_path, name="a"):
    return IncomingNote(
        name,
        make_wav(tmp_path / f"{name}.wav"),
        {
            "clock_synced": True,
            "book": "livro",
            "word_offset": 3,
            "excerpt": "o comeco do livro fala de outras coisas quaisquer",
        },
    )


# --- a conferencia de recall ------------------------------------------------


def test_a_recall_note_gets_checked_against_the_passage(tmp_path):
    cfg = vault_with_epub(tmp_path, [CH_ONE, CH_TWO])
    proc = SummarizingProcessor()
    path = process_note(
        recall_note(tmp_path), cfg, transcribe_fn=ok_transcribe(), processor=proc
    )
    assert proc.recall_calls
    spoken, passage = proc.recall_calls[0]
    assert "Falei o que entendi." in spoken
    assert "Flores" in passage

    text = path.read_text(encoding="utf-8")
    assert "Conferência do que você lembrou" in text
    assert "✓ **Você disse:** A física veio antes" in text
    assert "**Na verdade:** Alguns minutos depois" in text
    assert "Passou batido:** O trecho falava de Flores" in text


def test_a_note_that_is_not_a_recall_is_never_checked(tmp_path):
    cfg = vault_with_epub(tmp_path, [CH_ONE, CH_TWO])
    proc = SummarizingProcessor(kind="anotação")
    process_note(
        recall_note(tmp_path), cfg, transcribe_fn=ok_transcribe(), processor=proc
    )
    assert proc.recall_calls == []


def test_a_recall_without_a_book_does_not_explode(tmp_path):
    # Sem livro, o indice e o capitulo ficam None, e a checagem nao pode tropecar
    # -- nem ao montar o trecho, nem ao ficar sem um.
    cfg = make_cfg(tmp_path)
    proc = SummarizingProcessor()
    path = process_note(
        IncomingNote("n", make_wav(tmp_path / "n.wav"), {"clock_synced": True}),
        cfg,
        transcribe_fn=ok_transcribe(),
        processor=proc,
    )
    assert path.is_file()
    # A chamada acontece, com trecho vazio: e o que deixa o raciocinio e o
    # aprofundamento valerem sem ancora. Ver
    # test_a_recall_without_an_anchor_is_still_discussed.
    assert proc.recall_calls == [("Falei o que entendi.", "")]


def test_the_recall_window_starts_at_the_previous_recall(tmp_path):
    cfg = vault_with_epub(tmp_path, [CH_ONE, CH_TWO])
    proc = SummarizingProcessor()
    process_note(
        recall_note(tmp_path, "a", 12), cfg, transcribe_fn=ok_transcribe(), processor=proc
    )
    process_note(
        recall_note(tmp_path, "b", 20), cfg, transcribe_fn=ok_transcribe(), processor=proc
    )
    # A segunda janela e mais curta: comeca onde a primeira parou.
    assert len(proc.recall_calls[1][1]) < len(proc.recall_calls[0][1])


# --- o resumo do capitulo ---------------------------------------------------


def test_the_chapter_summary_is_written_and_fed_only_by_the_notes(tmp_path):
    cfg = vault_with_epub(tmp_path, [CH_ONE, CH_TWO])
    proc = SummarizingProcessor()
    process_note(
        recall_note(tmp_path), cfg, transcribe_fn=ok_transcribe(), processor=proc
    )

    summary = cfg.vault_path / "Livros" / "livro" / "Capítulos" / "02 - Dois.md"
    assert summary.is_file()
    text = summary.read_text(encoding="utf-8")
    assert "Sintese gerada do capitulo." in text
    assert "## Minhas observações" in text
    # O texto do livro nunca alimenta o resumo.
    entries = " ".join(proc.chapter_calls[0])
    assert "nivel do mar" not in entries


def test_the_chapter_summary_preserves_my_observations_across_notes(tmp_path):
    cfg = vault_with_epub(tmp_path, [CH_ONE, CH_TWO])
    proc = SummarizingProcessor()
    process_note(
        recall_note(tmp_path, "a"), cfg, transcribe_fn=ok_transcribe(), processor=proc
    )

    summary = cfg.vault_path / "Livros" / "livro" / "Capítulos" / "02 - Dois.md"
    summary.write_text(
        summary.read_text(encoding="utf-8") + "\nanotei isso à mão\n", encoding="utf-8"
    )
    process_note(
        recall_note(tmp_path, "b", 20), cfg, transcribe_fn=ok_transcribe(), processor=proc
    )
    assert "anotei isso à mão" in summary.read_text(encoding="utf-8")


def test_chapter_on_capitulo_skips_the_per_note_rebuild(tmp_path):
    cfg = with_summaries(
        vault_with_epub(tmp_path, [CH_ONE, CH_TWO]), enabled=True, chapter_on="capitulo"
    )
    proc = SummarizingProcessor()
    process_note(
        recall_note(tmp_path), cfg, transcribe_fn=ok_transcribe(), processor=proc
    )
    # Nenhuma chamada de resumo enquanto a leitura nao sai do capitulo.
    assert proc.chapter_calls == []


def test_a_failing_summary_never_costs_the_note(tmp_path):
    cfg = vault_with_epub(tmp_path, [CH_ONE, CH_TWO])
    proc = SummarizingProcessor(fail="chapter")
    path = process_note(
        recall_note(tmp_path), cfg, transcribe_fn=ok_transcribe(), processor=proc
    )
    assert path.is_file()
    assert "Conferência do que você lembrou" in path.read_text(encoding="utf-8")


def test_summaries_can_be_switched_off(tmp_path):
    cfg = with_summaries(vault_with_epub(tmp_path, [CH_ONE, CH_TWO]), enabled=False)
    proc = SummarizingProcessor()
    process_note(
        recall_note(tmp_path), cfg, transcribe_fn=ok_transcribe(), processor=proc
    )
    assert proc.chapter_calls == []
    assert not (cfg.vault_path / "Livros" / "livro" / "Capítulos").exists()


# --- o resumo do livro -----------------------------------------------------


def test_the_book_summary_waits_for_a_chapter_to_be_left_behind(tmp_path):
    cfg = vault_with_epub(tmp_path, [CH_ONE, CH_TWO])
    proc = SummarizingProcessor(kind="anotação")

    process_note(
        first_chapter_note(tmp_path), cfg, transcribe_fn=ok_transcribe(), processor=proc
    )
    assert proc.book_calls == []

    # Primeira nota do capitulo 2: o capitulo 1 ficou para tras.
    process_note(
        recall_note(tmp_path, "b"), cfg, transcribe_fn=ok_transcribe(), processor=proc
    )
    assert proc.book_calls
    book = cfg.vault_path / "Livros" / "livro" / "livro.md"
    assert "Um ponto importante do livro" in book.read_text(encoding="utf-8")


def test_the_book_summary_is_built_from_syntheses_not_notes(tmp_path):
    cfg = vault_with_epub(tmp_path, [CH_ONE, CH_TWO])
    proc = SummarizingProcessor(kind="anotação")
    process_note(
        first_chapter_note(tmp_path), cfg, transcribe_fn=ok_transcribe(), processor=proc
    )
    process_note(
        recall_note(tmp_path, "b"), cfg, transcribe_fn=ok_transcribe(), processor=proc
    )
    # Duas sinteses: o resumo do livro inclui o capitulo que acabou de receber
    # nota, e nao apenas os anteriores. E o que faz dele 'tudo o que registrei'.
    assert proc.book_calls[0] == ["Sintese gerada do capitulo."] * 2


def test_the_book_summary_lists_chapters_without_a_record(tmp_path):
    cfg = vault_with_epub(tmp_path, [CH_ONE, CH_TWO, CH_THREE])
    proc = SummarizingProcessor(kind="anotação")
    process_note(
        first_chapter_note(tmp_path), cfg, transcribe_fn=ok_transcribe(), processor=proc
    )
    process_note(
        recall_note(tmp_path, "b"), cfg, transcribe_fn=ok_transcribe(), processor=proc
    )
    book = (cfg.vault_path / "Livros" / "livro" / "livro.md").read_text(encoding="utf-8")
    # O capitulo 3 nunca recebeu nota, e essa lacuna e informacao: e onde a leitura
    # passou sem engajamento.
    assert "Capítulos sem registro" in book
    assert "Três" in book.split("Capítulos sem registro")[1]


def test_a_recall_without_an_anchor_is_still_discussed(tmp_path):
    """The gate used to be the anchor, and two of the three parts never needed it.

    This is the note that was lost and recovered: its sidecar had already been
    deleted when the transcription failed, so it came back with no reading
    position. What reached Telegram was the transcript and not one word about it.
    """
    cfg = make_cfg(tmp_path, pp_enabled=True)
    proc = SummarizingProcessor(
        recall=RecallCheck(
            reasoning="Seu raciocínio se sustenta.",
            deepening="O próximo fio a puxar.",
            outside_passage=True,
            no_passage=True,
        )
    )
    # No book, no word_offset, no excerpt: nothing to resolve a chapter from.
    incoming = IncomingNote(
        "sem-ancora",
        make_wav(tmp_path / "sem-ancora.wav"),
        {"clock_synced": True, "recorded_at": "2026-09-11T14:09:00"},
    )

    path = process_note(incoming, cfg, transcribe_fn=ok_transcribe(), processor=proc)

    assert proc.recall_calls == [("Falei o que entendi.", "")]
    body = path.read_text(encoding="utf-8")
    assert "Seu raciocínio se sustenta." in body
    assert "O próximo fio a puxar." in body
    assert "sem âncora" in body


def test_a_recall_with_an_anchor_still_gets_the_passage(tmp_path):
    """Guard for the change above: losing the passage here would silently turn
    every conference into a discussion."""
    cfg = vault_with_epub(tmp_path, [CH_ONE, CH_TWO])
    proc = SummarizingProcessor()

    process_note(recall_note(tmp_path), cfg, transcribe_fn=ok_transcribe(), processor=proc)

    assert len(proc.recall_calls) == 1
    assert proc.recall_calls[0][1].strip(), "o trecho lido tem de chegar ao modelo"
