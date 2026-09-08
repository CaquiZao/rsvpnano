"""Pluggable post-processing: turn a raw transcript into title, tags and clean text."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Protocol

from handy_bridge.config import PostProcessConfig


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
class PostProcessResult:
    title: str
    tags: list[str]
    cleaned: str
    tasks: list[Task] = field(default_factory=list)


class PostProcessor(Protocol):
    def process(self, transcript: str) -> PostProcessResult: ...

    def answer_tasks(self, questions: list[str], excerpt: str | None) -> list[Answer]: ...

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
