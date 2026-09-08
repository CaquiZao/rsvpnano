"""Pluggable post-processing: turn a raw transcript into title, tags and clean text."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from handy_bridge.config import PostProcessConfig


class PostProcessError(Exception):
    """Raised when a post-processing backend fails or returns unusable output."""


@dataclass(frozen=True)
class PostProcessResult:
    title: str
    tags: list[str]
    cleaned: str


class PostProcessor(Protocol):
    def process(self, transcript: str) -> PostProcessResult: ...


def build(cfg: PostProcessConfig) -> PostProcessor | None:
    """Return a processor, or None when post-processing is switched off."""
    if not cfg.enabled or cfg.backend == "none":
        return None
    if cfg.backend == "claude_cli":
        from handy_bridge.postprocess.claude_cli import ClaudeCliProcessor

        return ClaudeCliProcessor(cfg.model)
    raise PostProcessError(f"backend '{cfg.backend}' is not implemented yet")
