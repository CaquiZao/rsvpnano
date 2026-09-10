"""Orchestrate a single note: repair, transcribe, post-process, write."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable

from handy_bridge import chapters as chapters_mod
from handy_bridge import kanban, layout, summaries
from handy_bridge import wav as wav_mod
from handy_bridge.config import AsrConfig, Config
from handy_bridge.epub import EpubError, ensure_book_markdown, epub_source
from handy_bridge.kind import resolve_kind
from handy_bridge.note import NoteData, write_note
from handy_bridge.postprocess import (
    Answer,
    PostProcessError,
    PostProcessor,
    RecallCheck,
    Task,
)
from handy_bridge.telegram import build_message
from handy_bridge.transcriber import Transcription
from handy_bridge.transcriber import transcribe as default_transcribe

log = logging.getLogger(__name__)

EMPTY_BODY = "(transcrição vazia)"


@dataclass(frozen=True)
class IncomingNote:
    note_id: str
    wav_path: Path
    meta: dict


def resolve_recorded_at(meta: dict, arrived_at: datetime) -> tuple[datetime, bool]:
    """Return (timestamp, was_estimated).

    The board has no battery-backed RTC, so a recording made offline after a reboot
    carries no wall-clock time. In that case the device sends monotonic uptime and
    the bridge reconstructs the moment from when the upload arrived.
    """
    if meta.get("clock_synced"):
        raw = meta.get("recorded_at")
        if raw:
            try:
                return datetime.fromisoformat(str(raw)), False
            except ValueError:
                log.warning("unparseable recorded_at %r; falling back to arrival", raw)
        return arrived_at, True

    recorded_uptime = meta.get("uptime_ms")
    upload_uptime = meta.get("uptime_at_upload_ms")
    if recorded_uptime is not None and upload_uptime is not None:
        delta_ms = float(upload_uptime) - float(recorded_uptime)
        if delta_ms >= 0:
            return arrived_at - timedelta(milliseconds=delta_ms), True
    return arrived_at, True


def process_note(
    incoming: IncomingNote,
    cfg: Config,
    *,
    transcribe_fn: Callable[[Path, AsrConfig], Transcription] = default_transcribe,
    processor: PostProcessor | None = None,
    now: Callable[[], datetime] = datetime.now,
    telegram: object | None = None,
    threads: object | None = None,
) -> Path:
    arrived_at = now()

    try:
        wav_mod.repair_header(incoming.wav_path)
    except wav_mod.InvalidWav:
        log.warning("could not repair %s; transcribing as-is", incoming.wav_path)
    info = wav_mod.inspect(incoming.wav_path)

    transcription = transcribe_fn(incoming.wav_path, cfg.asr)
    raw_text = transcription.text.strip()

    recorded_at, estimated = resolve_recorded_at(incoming.meta, arrived_at)
    title = f"{recorded_at:%Y-%m-%d %H%M}"
    tags: list[str] = []
    body = raw_text or EMPTY_BODY

    tasks: list[Task] = []
    answers: list[Answer] = []
    # Resolved without the model so the spoken marker word still works when
    # post-processing is switched off or fails.
    note_kind = resolve_kind(raw_text, None)

    if processor is not None and raw_text:
        try:
            result = processor.process(raw_text)
            title = result.title or title
            tags = result.tags
            body = result.cleaned or raw_text
            tasks = result.tasks
            note_kind = result.kind
        except PostProcessError as exc:
            # A post-processing failure must never cost a note.
            log.warning("post-processing failed for %s: %s", incoming.note_id, exc)

    excerpt = incoming.meta.get("excerpt") or None
    questions = [task.text for task in tasks if task.answerable]
    if processor is not None and questions and cfg.post_process.answer_tasks:
        try:
            answers = processor.answer_tasks(questions, excerpt)
        except PostProcessError as exc:
            # Answers are a convenience; the note and the cards still go out.
            log.warning("answering failed for %s: %s", incoming.note_id, exc)

    book = incoming.meta.get("book") or None
    raw_offset = incoming.meta.get("word_offset")
    word_offset = int(raw_offset) if raw_offset is not None else None

    chapter = None
    index = None
    width = layout.MIN_CHAPTER_DIGITS
    if book:
        try:
            ensure_book_markdown(cfg.vault_path, book)
        except (EpubError, OSError) as exc:
            # Best-effort: the book text is a convenience, never a reason to lose a note.
            log.warning("could not convert book %r: %s", book, exc)
        # load_index swallows its own failures and returns None, so an unreadable
        # book costs the chapter fields and nothing else.
        index = chapters_mod.load_index(
            layout.source_dir(cfg.vault_path, book),
            book,
            epub_source(cfg.vault_path, book),
        )
        if index is not None:
            width = index.width
            chapter = chapters_mod.resolve(index, excerpt, word_offset)

    recall = RecallCheck()
    if note_kind == "recall" and processor is not None and index and chapter:
        try:
            passage = chapters_mod.passage(
                index,
                chapter.chapter,
                _last_recall_offset(cfg, book, chapter.chapter),
                word_offset,
            )
            recall = processor.check_recall(body, passage)
        except PostProcessError as exc:
            # The check is a convenience; the note and its cards still go out.
            log.warning("recall check failed for %s: %s", incoming.note_id, exc)

    note_path = write_note(
        layout.notes_dir(cfg.vault_path, book),
        NoteData(
            title=title,
            tags=tags,
            body=body,
            raw_transcript=raw_text,
            recorded_at=recorded_at,
            date_estimated=estimated,
            duration_s=info.duration_s,
            asr_model=transcription.model,
            kind=note_kind,
            # Anchor fields arrive only when the device recorded from the reader.
            book=book,
            word_offset=word_offset,
            excerpt=excerpt,
            chapter=chapter.chapter if chapter else None,
            chapter_title=chapter.title if chapter else None,
            chapter_source=chapter.source if chapter else None,
            answers=[(a.question, a.answer) for a in answers],
            recall_points=[(p.said, p.actual, p.correct) for p in recall.points],
            recall_missed=list(recall.missed),
        ),
        width=width,
    )

    # Everything below is best-effort: the note is already safe on disk.
    if cfg.summaries.enabled and book and chapter and processor is not None:
        try:
            _refresh_summaries(cfg, book, chapter.chapter, index, processor)
        except (PostProcessError, OSError) as exc:
            # An old summary beats a lost note, so this never propagates.
            log.warning("could not refresh summaries for %s: %s", book, exc)

    if cfg.kanban.enabled and tasks:
        try:
            _update_board(cfg, book, note_path.stem, tasks, answers)
        except (kanban.KanbanError, OSError) as exc:
            log.warning("could not update the Kanban board for %s: %s", incoming.note_id, exc)

    if telegram is not None and answers:
        for answer in answers:
            try:
                message_id = telegram.send(build_message(answer.question, answer.answer, book))
            except Exception as exc:
                log.warning("could not deliver an answer over Telegram: %s", exc)
                continue
            # Remember which note this message belongs to, so replying to it on the
            # phone lands the follow-up in the right place.
            if threads is not None and message_id:
                threads.remember(message_id, note_path, answer.question, answer.answer)

    return note_path


def _update_board(
    cfg: Config,
    book: str | None,
    note_name: str,
    tasks: list[Task],
    answers: list[Answer],
) -> None:
    """Route each task to the lane its certainty deserves."""
    answered = {answer.question for answer in answers}
    by_lane: dict[str, list[str]] = {}
    for task in tasks:
        is_done = task.text in answered
        if is_done:
            lane = kanban.DONE_LANE          # already resolved, nothing left to do
        elif task.source == "keyword":
            lane = kanban.TODO_LANE          # the speaker asked for it outright
        else:
            lane = kanban.TRIAGE_LANE        # inferred, so it waits for a human look
        by_lane.setdefault(lane, []).append(
            kanban.render_card(task.text, note_name, done=is_done)
        )

    board = kanban.board_path_for(cfg.vault_path, cfg.kanban.subfolder, book)
    kanban.ensure_board(board)
    for lane, cards in by_lane.items():
        kanban.add_cards(board, lane, cards)


def _offset_from_stem(stem: str) -> int | None:
    """Read the reading position back out of a note's filename.

    The name is `<chapter>-<offset> <title>`, so the offset is already there and
    reading the file to find it would be wasted work.
    """
    head = stem.split(" ", 1)[0]
    _, _, offset = head.partition("-")
    return int(offset) if offset.isdigit() else None


def _last_recall_offset(cfg: Config, book: str, chapter: int) -> int | None:
    """Where the previous recall in this chapter left off.

    None means "no earlier recall", which makes the passage window the whole
    chapter — the right default for the first recall of a reading run.
    """
    notes = summaries.collect_chapter_notes(
        layout.notes_dir(cfg.vault_path, book), chapter
    )
    offsets = [
        offset
        for note in notes
        if note.kind == "recall"
        for offset in [_offset_from_stem(note.stem)]
        if offset is not None
    ]
    return max(offsets) if offsets else None


def _refresh_summaries(
    cfg: Config,
    book: str,
    chapter: int,
    index: "chapters_mod.BookIndex | None",
    processor: PostProcessor,
) -> None:
    """Rebuild the chapter summary, and the book's when a chapter is left behind."""
    chapters_dir = layout.chapters_dir(cfg.vault_path, book)
    width = index.width if index else layout.MIN_CHAPTER_DIGITS
    title = ""
    if index is not None:
        found = index.get(chapter)
        title = found.title if found else ""
    title = title or f"Capítulo {chapter}"

    already = {number for number, _, _ in summaries.collect_syntheses(chapters_dir)}
    advanced = bool(already) and chapter > max(already)

    # With chapter_on="capitulo" the current chapter's summary waits until the
    # reading moves past it, trading freshness for roughly one call per chapter
    # instead of one per note.
    if cfg.summaries.chapter_on == "nota" or advanced:
        _write_chapter_summary(cfg, book, chapter, title, width, processor)

    if not advanced:
        return

    # The book summary is rebuilt from the syntheses, never from the raw notes:
    # that is what keeps its input proportional to the chapter count and what
    # lets the important points be re-ranked as the reading goes on.
    syntheses = summaries.collect_syntheses(chapters_dir)
    bullets = processor.summarize_book([text for _, _, text in syntheses])
    unrecorded = summaries.unrecorded_chapters(index, chapters_dir) if index else []
    target = layout.book_summary_path(cfg.vault_path, book)
    summaries.write_chapter_summary(
        target,
        summaries.render_book(
            title=book, bullets=bullets, syntheses=syntheses, unrecorded=unrecorded
        ),
    )


def _write_chapter_summary(
    cfg: Config,
    book: str,
    chapter: int,
    title: str,
    width: int,
    processor: PostProcessor,
) -> None:
    notes = summaries.collect_chapter_notes(
        layout.notes_dir(cfg.vault_path, book), chapter
    )
    if not notes:
        return
    target = layout.chapter_summary_path(cfg.vault_path, book, chapter, title, width)
    # Read the reserved section back before overwriting, so a hand-written note
    # inside a generated file survives.
    observations = summaries.read_observations(target)
    synthesis = processor.summarize_chapter(summaries.synthesis_input(notes))
    summaries.write_chapter_summary(
        target,
        summaries.render_chapter(
            chapter=chapter,
            title=title,
            synthesis=synthesis,
            sections=summaries.sections_from(notes),
            observations=observations,
        ),
    )
