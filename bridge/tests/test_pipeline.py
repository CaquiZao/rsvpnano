import struct
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from handy_bridge import layout
from handy_bridge.config import AsrConfig, Config, PostProcessConfig
from handy_bridge.pipeline import IncomingNote, process_note, resolve_recorded_at
from handy_bridge.postprocess import PostProcessError, PostProcessResult
from handy_bridge.transcriber import Transcription, TranscriptionError
from test_epub import make_epub_with

ARRIVED = datetime(2026, 9, 7, 15, 0, 0)


def make_wav(path: Path, seconds: int = 1) -> Path:
    frames = 16000 * seconds
    data = b"\x00\x01" * frames
    path.write_bytes(
        b"RIFF"
        + struct.pack("<I", 36 + len(data))
        + b"WAVE"
        + b"fmt "
        + struct.pack("<IHHIIHH", 16, 1, 1, 16000, 32000, 2, 16)
        + b"data"
        + struct.pack("<I", len(data))
        + data
    )
    return path


def make_cfg(tmp_path, *, pp_enabled=True) -> Config:
    vault = tmp_path / "Reading"
    vault.mkdir(exist_ok=True)
    return Config(
        vault_path=vault,
        inbox_folder="Inbox",
        audio_store=tmp_path / "audio",
        port=8787,
        asr=AsrConfig(handy_exe=tmp_path / "handy.exe", model="m.gguf", timeout_s=900),
        post_process=PostProcessConfig(
            enabled=pp_enabled, backend="claude_cli", model="haiku"
        ),
    )


class FakeProcessor:
    def __init__(self, result=None, error=None):
        self.result, self.error = result, error

    def process(self, transcript):
        if self.error:
            raise self.error
        return self.result


def ok_transcribe(text="ola mundo"):
    return lambda wav, cfg: Transcription(
        text=text, audio_secs=1.0, rtf=1.3, model="m.gguf"
    )


# --- clock reconstruction -------------------------------------------------


def test_uses_device_clock_when_synced():
    meta = {"clock_synced": True, "recorded_at": "2026-09-07T14:32:11"}
    when, estimated = resolve_recorded_at(meta, ARRIVED)
    assert when == datetime(2026, 9, 7, 14, 32, 11)
    assert estimated is False


def test_reconstructs_time_when_clock_not_synced():
    # gravado 5 minutos de uptime antes do upload
    meta = {"clock_synced": False, "uptime_ms": 60_000, "uptime_at_upload_ms": 360_000}
    when, estimated = resolve_recorded_at(meta, ARRIVED)
    assert when == ARRIVED - timedelta(milliseconds=300_000)
    assert estimated is True


def test_falls_back_to_arrival_when_uptime_missing():
    when, estimated = resolve_recorded_at({"clock_synced": False}, ARRIVED)
    assert when == ARRIVED
    assert estimated is True


def test_falls_back_to_arrival_when_recorded_at_unparseable():
    meta = {"clock_synced": True, "recorded_at": "not-a-date"}
    when, estimated = resolve_recorded_at(meta, ARRIVED)
    assert when == ARRIVED
    assert estimated is True


# --- orchestration --------------------------------------------------------


def test_writes_note_with_post_processed_fields(tmp_path):
    cfg = make_cfg(tmp_path)
    wav = make_wav(tmp_path / "n.wav")
    incoming = IncomingNote(
        "n", wav, {"clock_synced": True, "recorded_at": "2026-09-07T14:32:11"}
    )
    processor = FakeProcessor(
        PostProcessResult("Meu titulo", ["ideia"], "Texto limpo.")
    )

    path = process_note(
        incoming, cfg, transcribe_fn=ok_transcribe(), processor=processor
    )
    text = path.read_text(encoding="utf-8")

    # O nome e a data da gravacao; a posicao de leitura vive no frontmatter.
    assert path.name == "2026-09-07 1432 - Meu titulo.md"
    assert "Texto limpo." in text
    assert "> ola mundo" in text  # transcrição crua preservada
    assert "tags: [ideia]" in text


def test_degrades_to_raw_transcript_when_post_processing_fails(tmp_path):
    cfg = make_cfg(tmp_path)
    incoming = IncomingNote(
        "n",
        make_wav(tmp_path / "n.wav"),
        {"clock_synced": True, "recorded_at": "2026-09-07T14:32:11"},
    )
    processor = FakeProcessor(error=PostProcessError("boom"))

    path = process_note(
        incoming, cfg, transcribe_fn=ok_transcribe(), processor=processor
    )
    text = path.read_text(encoding="utf-8")

    assert path.exists()
    assert "ola mundo" in text
    assert "2026-09-07 1432" in path.name


def test_works_with_post_processing_disabled(tmp_path):
    cfg = make_cfg(tmp_path, pp_enabled=False)
    incoming = IncomingNote(
        "n",
        make_wav(tmp_path / "n.wav"),
        {"clock_synced": True, "recorded_at": "2026-09-07T14:32:11"},
    )
    path = process_note(incoming, cfg, transcribe_fn=ok_transcribe(), processor=None)
    assert "ola mundo" in path.read_text(encoding="utf-8")


def test_empty_transcription_still_writes_a_note(tmp_path):
    cfg = make_cfg(tmp_path)
    incoming = IncomingNote(
        "n",
        make_wav(tmp_path / "n.wav"),
        {"clock_synced": True, "recorded_at": "2026-09-07T14:32:11"},
    )
    path = process_note(incoming, cfg, transcribe_fn=ok_transcribe(""), processor=None)
    assert path.exists()
    assert "(transcrição vazia)" in path.read_text(encoding="utf-8")


def test_repairs_truncated_wav_before_transcribing(tmp_path):
    cfg = make_cfg(tmp_path)
    wav = make_wav(tmp_path / "n.wav")
    # zera os tamanhos, simulando queda de energia durante a gravação
    with wav.open("r+b") as fh:
        fh.seek(4)
        fh.write(struct.pack("<I", 0))
        fh.seek(40)
        fh.write(struct.pack("<I", 0))

    incoming = IncomingNote(
        "n", wav, {"clock_synced": True, "recorded_at": "2026-09-07T14:32:11"}
    )
    path = process_note(incoming, cfg, transcribe_fn=ok_transcribe(), processor=None)

    assert path.exists()
    assert struct.unpack_from("<I", wav.read_bytes(), 40)[0] == 32000


def test_carries_book_anchor_from_meta_into_the_note(tmp_path):
    cfg = make_cfg(tmp_path)
    incoming = IncomingNote(
        "n",
        make_wav(tmp_path / "n.wav"),
        {
            "clock_synced": True,
            "recorded_at": "2026-09-07T14:32:11",
            "book": "epdf.pub_sapiens",
            "word_offset": 12438,
            "excerpt": "a Revolução Agrícola foi a maior fraude da história",
        },
    )
    path = process_note(incoming, cfg, transcribe_fn=ok_transcribe(), processor=None)
    text = path.read_text(encoding="utf-8")

    assert 'book: "[[epdf.pub_sapiens]]"' in text
    assert "word_offset: 12438" in text
    assert "> [!quote] Trecho que eu estava lendo" in text


def test_standalone_note_has_no_anchor(tmp_path):
    cfg = make_cfg(tmp_path)
    incoming = IncomingNote(
        "n",
        make_wav(tmp_path / "n.wav"),
        {"clock_synced": True, "recorded_at": "2026-09-07T14:32:11"},
    )
    text = process_note(
        incoming, cfg, transcribe_fn=ok_transcribe(), processor=None
    ).read_text(encoding="utf-8")
    assert "book:" not in text
    assert "[!quote]" not in text


def test_transcription_failure_propagates(tmp_path):
    cfg = make_cfg(tmp_path)
    incoming = IncomingNote("n", make_wav(tmp_path / "n.wav"), {"clock_synced": True})

    def boom(wav, asr_cfg):
        raise TranscriptionError("handy exploded")

    with pytest.raises(TranscriptionError):
        process_note(incoming, cfg, transcribe_fn=boom, processor=None)


# --- tarefas, quadro e telegram ------------------------------------------

from handy_bridge import kanban  # noqa: E402
from handy_bridge.config import KanbanConfig, TelegramConfig  # noqa: E402
from handy_bridge.postprocess import Answer, PostProcessResult as PPR, Task  # noqa: E402


class RichProcessor:
    """Dublê que registra as chamadas de resposta em lote."""

    def __init__(self, result, answers=None, answer_error=None):
        self.result = result
        self.answers = answers or []
        self.answer_error = answer_error
        self.answer_calls = []

    def process(self, transcript):
        return self.result

    def answer_tasks(self, questions, excerpt):
        self.answer_calls.append((list(questions), excerpt))
        if self.answer_error:
            raise self.answer_error
        return self.answers


class RecordingTelegram:
    def __init__(self, error=None):
        self.sent = []
        self.replies = []
        self.error = error

    def send(self, text, reply_to=None):
        if self.error:
            raise self.error
        self.sent.append(text)
        self.replies.append(reply_to)
        return len(self.sent)


def cfg_with(tmp_path, *, kanban_on=True):
    cfg = make_cfg(tmp_path)
    return Config(
        vault_path=cfg.vault_path,
        inbox_folder=cfg.inbox_folder,
        audio_store=cfg.audio_store,
        port=cfg.port,
        asr=cfg.asr,
        post_process=cfg.post_process,
        kanban=KanbanConfig(enabled=kanban_on, subfolder="Quadros"),
        telegram=TelegramConfig(),
    )


def reading_note(tmp_path, name="n"):
    return IncomingNote(
        name,
        make_wav(tmp_path / f"{name}.wav"),
        {
            "clock_synced": True,
            "recorded_at": "2026-09-07T14:32:11",
            "book": "sapiens",
            "excerpt": "a Revolução Agrícola",
        },
    )


def test_answerable_task_is_answered_and_lands_in_the_note(tmp_path):
    cfg = cfg_with(tmp_path)
    proc = RichProcessor(
        PPR("T", [], "C", tasks=[Task("O que foi o Big Bang?", "keyword", True)]),
        answers=[Answer("O que foi o Big Bang?", "O evento inicial.")],
    )
    path = process_note(reading_note(tmp_path), cfg, transcribe_fn=ok_transcribe(), processor=proc)

    assert proc.answer_calls == [(["O que foi o Big Bang?"], "a Revolução Agrícola")]
    text = path.read_text(encoding="utf-8")
    assert "> [!question] O que foi o Big Bang?" in text
    assert "O evento inicial." in text


def test_non_answerable_task_does_not_trigger_a_second_call(tmp_path):
    cfg = cfg_with(tmp_path)
    proc = RichProcessor(PPR("T", [], "C", tasks=[Task("Reler o capitulo", "keyword", False)]))
    process_note(reading_note(tmp_path), cfg, transcribe_fn=ok_transcribe(), processor=proc)
    assert proc.answer_calls == []


def test_cards_land_in_the_lane_that_matches_their_source(tmp_path):
    cfg = cfg_with(tmp_path)
    proc = RichProcessor(
        PPR("T", [], "C", tasks=[
            Task("Explicita", "keyword", False),
            Task("Inferida", "inferred", False),
            Task("Respondida", "keyword", True),
        ]),
        answers=[Answer("Respondida", "pronto")],
    )
    process_note(reading_note(tmp_path), cfg, transcribe_fn=ok_transcribe(), processor=proc)

    board = (cfg.vault_path / "Livros" / "sapiens" / "Quadro.md").read_text(encoding="utf-8")
    lines = board.splitlines()

    def lane_cards(lane):
        at = lines.index(f"## {lane}")
        out = []
        for line in lines[at + 1 :]:
            if line.startswith("## ") or line.startswith("%%"):
                break
            if line.startswith("- [ ]"):
                out.append(line)
        return out

    assert any("Explicita" in c for c in lane_cards(kanban.TODO_LANE))
    assert any("Inferida" in c for c in lane_cards(kanban.TRIAGE_LANE))
    # A respondida ja esta resolvida: vai para Concluido E vem marcada
    at = lines.index(f"## {kanban.DONE_LANE}")
    done = [ln for ln in lines[at + 1 :] if ln.startswith("- [")]
    assert any("Respondida" in c and c.startswith("- [x]") for c in done)


def test_card_links_back_to_the_note(tmp_path):
    cfg = cfg_with(tmp_path)
    proc = RichProcessor(PPR("Meu titulo", [], "C", tasks=[Task("Uma tarefa", "keyword", False)]))
    path = process_note(reading_note(tmp_path), cfg, transcribe_fn=ok_transcribe(), processor=proc)
    board = (cfg.vault_path / "Livros" / "sapiens" / "Quadro.md").read_text(encoding="utf-8")
    assert f"[[{path.stem}]]" in board


def test_note_without_a_book_uses_the_general_board(tmp_path):
    cfg = cfg_with(tmp_path)
    incoming = IncomingNote(
        "n", make_wav(tmp_path / "n.wav"),
        {"clock_synced": True, "recorded_at": "2026-09-07T14:32:11"},
    )
    proc = RichProcessor(PPR("T", [], "C", tasks=[Task("Solta", "keyword", False)]))
    process_note(incoming, cfg, transcribe_fn=ok_transcribe(), processor=proc)
    assert (cfg.vault_path / "Geral" / "Quadro.md").exists()


def test_kanban_disabled_creates_no_board(tmp_path):
    cfg = cfg_with(tmp_path, kanban_on=False)
    proc = RichProcessor(PPR("T", [], "C", tasks=[Task("Uma", "keyword", False)]))
    process_note(reading_note(tmp_path), cfg, transcribe_fn=ok_transcribe(), processor=proc)
    assert not (cfg.vault_path / "Quadros").exists()


def test_no_tasks_creates_no_board(tmp_path):
    cfg = cfg_with(tmp_path)
    proc = RichProcessor(PPR("T", [], "C", tasks=[]))
    process_note(reading_note(tmp_path), cfg, transcribe_fn=ok_transcribe(), processor=proc)
    assert not (cfg.vault_path / "Quadros").exists()


def test_answering_failure_still_writes_the_note(tmp_path):
    cfg = cfg_with(tmp_path)
    proc = RichProcessor(
        PPR("T", [], "C", tasks=[Task("q", "keyword", True)]),
        answer_error=PostProcessError("modelo caiu"),
    )
    path = process_note(reading_note(tmp_path), cfg, transcribe_fn=ok_transcribe(), processor=proc)
    assert path.exists()
    assert "[!question]" not in path.read_text(encoding="utf-8")


def test_telegram_receives_the_answer(tmp_path):
    cfg = cfg_with(tmp_path)
    proc = RichProcessor(
        PPR("T", [], "C", tasks=[Task("O que foi o Big Bang?", "keyword", True)]),
        answers=[Answer("O que foi o Big Bang?", "O evento inicial.")],
    )
    tg = RecordingTelegram()
    process_note(reading_note(tmp_path), cfg, transcribe_fn=ok_transcribe(), processor=proc, telegram=tg)
    # Duas mensagens agora: o aviso de chegada primeiro, a resposta depois.
    assert len(tg.sent) == 2
    assert tg.sent[0].startswith("📝") or tg.sent[0].startswith("❓") or tg.sent[0].startswith("🔁")
    assert "O que foi o Big Bang?" in tg.sent[1]
    assert "O evento inicial." in tg.sent[1]
    # E a resposta vem aninhada sob o aviso, para a ordem na tela bater com a real.
    assert tg.replies[0] is None
    assert tg.replies[1] == 1


def test_telegram_failure_still_writes_the_note(tmp_path):
    cfg = cfg_with(tmp_path)
    proc = RichProcessor(
        PPR("T", [], "C", tasks=[Task("q", "keyword", True)]),
        answers=[Answer("q", "a")],
    )
    tg = RecordingTelegram(error=RuntimeError("sem rede"))
    path = process_note(reading_note(tmp_path), cfg, transcribe_fn=ok_transcribe(), processor=proc, telegram=tg)
    assert path.exists()
    assert "[!question] q" in path.read_text(encoding="utf-8")


def test_telegram_announces_a_note_even_with_nothing_answered(tmp_path):
    # Antes o bot ficava calado quando nao havia resposta a entregar, e a maioria
    # das notas nunca aparecia no celular -- uma nota que voce nao ve e uma nota
    # sobre a qual voce nao consegue perguntar depois.
    cfg = cfg_with(tmp_path)
    proc = RichProcessor(PPR("T", [], "C", tasks=[Task("x", "inferred", False)]))
    tg = RecordingTelegram()
    process_note(reading_note(tmp_path), cfg, transcribe_fn=ok_transcribe(), processor=proc, telegram=tg)
    assert len(tg.sent) == 1
    assert "T" in tg.sent[0]
    assert tg.replies == [None]


# --- layout por livro e resolucao de capitulo -------------------------------


def vault_with_epub(tmp_path, bodies, stem="livro") -> Config:
    """Config whose vault already holds a book, in the migrated layout."""
    cfg = make_cfg(tmp_path)
    source = layout.source_dir(cfg.vault_path, stem)
    source.mkdir(parents=True, exist_ok=True)
    make_epub_with(source / f"{stem}.epub", bodies)
    return cfg


CH_ONE = ("c1.xhtml", "<h1>Um</h1><p>o comeco do livro fala de outras coisas quaisquer</p>")
CH_TWO = (
    "c2.xhtml",
    "<h1>Dois</h1><p>humanos chegaram a ilha de Flores quando o nivel do mar"
    " estava excepcionalmente baixo</p>",
)


def test_process_note_writes_into_the_book_layout_with_a_resolved_chapter(tmp_path):
    cfg = vault_with_epub(tmp_path, [CH_ONE, CH_TWO])
    incoming = IncomingNote(
        "n",
        make_wav(tmp_path / "n.wav"),
        {
            "clock_synced": True,
            "recorded_at": "2026-09-09T22:05:40",
            "book": "livro",
            "word_offset": 1324,
            # Acentos, pontuacao e caixa diferentes do markdown convertido.
            "excerpt": "Humanos chegaram à ilha de Flores, quando o nível do mar"
            " estava excepcionalmente baixo!",
        },
    )
    path = process_note(
        incoming,
        cfg,
        transcribe_fn=ok_transcribe("recapitulando o capitulo"),
        processor=None,
    )

    # A transcricao diz "recapitulando", entao a nota e um recall e vai para a
    # pasta desse tipo.
    assert path.parent == cfg.vault_path / "Livros" / "livro" / "Recall"
    assert path.name == "2026-09-09 2205 - 2026-09-09 2205.md"
    text = path.read_text(encoding="utf-8")
    assert "chapter: 2" in text
    assert 'chapter_title: "Dois"' in text
    assert "chapter_source: exato" in text
    # Sem processor, o override falado ainda tem de valer.
    assert "kind: recall" in text


def test_process_note_falls_back_to_an_estimate_without_a_usable_excerpt(tmp_path):
    cfg = vault_with_epub(tmp_path, [CH_ONE, CH_TWO])
    incoming = IncomingNote(
        "n",
        make_wav(tmp_path / "n.wav"),
        {"clock_synced": True, "book": "livro", "word_offset": 2, "excerpt": ""},
    )
    text = process_note(
        incoming, cfg, transcribe_fn=ok_transcribe(), processor=None
    ).read_text(encoding="utf-8")
    assert "chapter_source: estimado" in text


def test_process_note_without_a_book_lands_in_geral(tmp_path):
    cfg = make_cfg(tmp_path)
    incoming = IncomingNote(
        "n", make_wav(tmp_path / "n.wav"), {"clock_synced": True}
    )
    path = process_note(incoming, cfg, transcribe_fn=ok_transcribe(), processor=None)
    assert path.parent == cfg.vault_path / "Geral" / "Anotações"
    assert "chapter:" not in path.read_text(encoding="utf-8")


def test_a_broken_epub_still_produces_a_note(tmp_path):
    # Regra global: uma conveniencia quebrada nunca custa uma nota.
    cfg = make_cfg(tmp_path)
    source = layout.source_dir(cfg.vault_path, "livro")
    source.mkdir(parents=True, exist_ok=True)
    (source / "livro.epub").write_bytes(b"nao sou um zip")
    incoming = IncomingNote(
        "n",
        make_wav(tmp_path / "n.wav"),
        {"clock_synced": True, "book": "livro", "excerpt": "qualquer coisa aqui"},
    )
    path = process_note(incoming, cfg, transcribe_fn=ok_transcribe(), processor=None)
    assert path.is_file()
    assert "chapter:" not in path.read_text(encoding="utf-8")


def test_an_unmigrated_vault_still_finds_the_epub_at_the_root(tmp_path):
    # A propria migracao precisa ler o epub para resolver capitulos, entao o
    # fallback para a raiz do vault nao e cortesia: e pre-requisito dela.
    cfg = make_cfg(tmp_path)
    make_epub_with(cfg.vault_path / "livro.epub", [CH_ONE, CH_TWO])
    incoming = IncomingNote(
        "n",
        make_wav(tmp_path / "n.wav"),
        {
            "clock_synced": True,
            "book": "livro",
            "word_offset": 5,
            "excerpt": "humanos chegaram a ilha de Flores quando o nivel do mar",
        },
    )
    text = process_note(
        incoming, cfg, transcribe_fn=ok_transcribe(), processor=None
    ).read_text(encoding="utf-8")
    assert "chapter: 2" in text and "chapter_source: exato" in text


def test_two_notes_at_the_same_position_do_not_collide(tmp_path):
    # As notas reais de 14:00 e 16:10 compartilham o offset 8120.
    cfg = vault_with_epub(tmp_path, [CH_ONE, CH_TWO])
    meta = {
        "clock_synced": True,
        "book": "livro",
        "word_offset": 1324,
        "excerpt": "humanos chegaram a ilha de Flores quando o nivel do mar",
    }
    first = process_note(
        IncomingNote("a", make_wav(tmp_path / "a.wav"), meta),
        cfg, transcribe_fn=ok_transcribe(), processor=None,
    )
    second = process_note(
        IncomingNote("b", make_wav(tmp_path / "b.wav"), meta),
        cfg, transcribe_fn=ok_transcribe(), processor=None,
    )
    assert first != second and second.name.endswith("-2.md")


def test_a_note_creates_every_kind_directory(tmp_path):
    cfg = vault_with_epub(tmp_path, [CH_ONE, CH_TWO])
    process_note(
        IncomingNote("n", make_wav(tmp_path / "n.wav"), {
            "clock_synced": True, "book": "livro", "word_offset": 3,
            "excerpt": "o comeco do livro fala de outras coisas quaisquer",
        }),
        cfg, transcribe_fn=ok_transcribe(), processor=None,
    )
    book = cfg.vault_path / "Livros" / "livro"
    # Conjunto, nao lista: a ordem de Path.iterdir depende da plataforma.
    assert {p.name for p in book.iterdir() if p.is_dir()} == {
        "Anotações",
        "Perguntas",
        "Recall",
        "fonte",
    }


def test_telegram_answer_does_not_echo_the_whole_recording(tmp_path):
    # Nota do tipo pergunta sem pergunta extraida: o fallback manda o corpo todo
    # para o modelo, e a resposta chegava reimprimindo a transcricao que o aviso
    # tinha acabado de mostrar.
    cfg = cfg_with(tmp_path)
    corpo = "Gostaria de saber como vai o andamento do relatorio."
    proc = RichProcessor(
        PPR("Consulta sobre o relatorio", [], corpo, kind="pergunta"),
        answers=[Answer(corpo, "Nao tenho relatorio algum.")],
    )
    tg = RecordingTelegram()
    process_note(reading_note(tmp_path), cfg, transcribe_fn=ok_transcribe(), processor=proc, telegram=tg)

    assert len(tg.sent) == 2
    aviso, resposta = tg.sent
    # O aviso leva a transcricao do que foi falado.
    assert "ola mundo" in aviso
    # A resposta vem sem repetir a pergunta, porque a pergunta era a nota.
    assert "❓" not in resposta
    assert "Nao tenho relatorio algum." in resposta


def test_telegram_answer_still_shows_a_real_question(tmp_path):
    cfg = cfg_with(tmp_path)
    proc = RichProcessor(
        PPR("T", [], "corpo diferente da pergunta", tasks=[Task("O que foi o Big Bang?", "keyword", True)]),
        answers=[Answer("O que foi o Big Bang?", "O evento inicial.")],
    )
    tg = RecordingTelegram()
    process_note(reading_note(tmp_path), cfg, transcribe_fn=ok_transcribe(), processor=proc, telegram=tg)
    assert "❓ O que foi o Big Bang?" in tg.sent[1]
