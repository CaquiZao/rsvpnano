"""Pluggable post-processing: turn a raw transcript into title, tags and clean text."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Protocol

from handy_bridge.config import PostProcessConfig
from handy_bridge.kind import DEFAULT_KIND, NoteKind


class PostProcessError(Exception):
    """Raised when a post-processing backend fails or returns unusable output."""


# "keyword" means the speaker used an explicit marker word and meant it as a task;
# "inferred" means the model spotted an intention and is less sure, so the card
# lands in the triage lane instead of straight into the to-do lane.
TaskSource = Literal["keyword", "inferred"]


@dataclass(frozen=True)
class Task:
    text: str
    source: TaskSource = "inferred"
    answerable: bool = False


@dataclass(frozen=True)
class Answer:
    question: str
    answer: str


@dataclass(frozen=True)
class RecallPoint:
    """One claim the speaker made, and what the book actually says."""

    said: str
    actual: str
    correct: bool


@dataclass(frozen=True)
class RecallCheck:
    points: list[RecallPoint] = field(default_factory=list)
    # Things in the passage the speaker did not mention at all.
    missed: list[str] = field(default_factory=list)
    # Como o pensamento se sustentou, e onde escorregou. Julga o raciocinio em vez
    # da memoria, e por isso pode usar conhecimento alem do trecho.
    reasoning: str = ""
    # Tres a cinco frases estendendo o fio que a pessoa puxou.
    deepening: str = ""
    # True quando a recordacao trata de material que o trecho nao cobre -- um
    # capitulo anterior, ou a moldura do livro. A conferencia factual e descartada
    # nesse caso, porque nao ha contra o que confiri-la.
    outside_passage: bool = False
    # True quando nao havia trecho nenhum: a gravacao chegou sem ancora de leitura.
    # Implica outside_passage, e existe separado dele porque o motivo que a nota
    # mostra e diferente -- "o trecho nao cobre isso" seria mentira quando nao
    # houve trecho.
    no_passage: bool = False

    def __bool__(self) -> bool:
        return bool(self.points or self.missed or self.reasoning or self.deepening)


@dataclass(frozen=True)
class PostProcessResult:
    title: str
    tags: list[str]
    cleaned: str
    tasks: list[Task] = field(default_factory=list)
    # Questions asked out loud, kept separate from `tasks` on purpose. A task is
    # something to do; a question is something to answer. Conflating them meant
    # answering depended on the deliberately conservative task extraction, so a
    # recording plainly classified as a question could come back unanswered.
    questions: list[str] = field(default_factory=list)
    # Defaulted so a backend that does not classify still returns a usable result.
    kind: NoteKind = DEFAULT_KIND


class PostProcessor(Protocol):
    def process(self, transcript: str) -> PostProcessResult: ...

    def answer_tasks(self, questions: list[str], excerpt: str | None) -> list[Answer]: ...

    def check_recall(self, spoken: str, passage: str) -> RecallCheck: ...

    def summarize_chapter(self, entries: list[str]) -> str: ...

    def summarize_book(self, syntheses: list[str]) -> list[str]: ...

    def answer_followup(
        self, question: str, history: list[tuple[str, str]], excerpt: str | None
    ) -> str: ...


def build(cfg: PostProcessConfig) -> PostProcessor | None:
    """Return a processor, or None when post-processing is switched off."""
    if not cfg.enabled or cfg.backend == "none":
        return None
    if cfg.backend == "claude_cli":
        from handy_bridge.postprocess.claude_cli import ClaudeCliProcessor

        return ClaudeCliProcessor(cfg.model)
    raise PostProcessError(f"backend '{cfg.backend}' is not implemented yet")
