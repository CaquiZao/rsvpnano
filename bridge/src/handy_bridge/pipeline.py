"""Orchestrate a single note: repair, transcribe, post-process, write."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable

from handy_bridge import chapters as chapters_mod
from handy_bridge import kanban, layout
from handy_bridge import wav as wav_mod
from handy_bridge.config import AsrConfig, Config
from handy_bridge.epub import EpubError, ensure_book_markdown, epub_source
from handy_bridge.kind import resolve_kind
from handy_bridge.note import NoteData, write_note
from handy_bridge.postprocess import Answer, PostProcessError, PostProcessor, Task
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
        ),
        width=width,
    )

    # Everything below is best-effort: the note is already safe on disk.
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
