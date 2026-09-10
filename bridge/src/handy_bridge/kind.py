"""Decide what a recording was for: a note, a question, or a memory check.

Two layers, on purpose. The model infers the kind inside the JSON it already
returns, which costs nothing extra; a spoken marker word then overrides it in
code. The model tolerates phrasing the regex never anticipated, and the regex
gives the determinism a marker word exists to provide.
"""

from __future__ import annotations

import re
from typing import Literal

NoteKind = Literal["anotação", "pergunta", "recall"]

VALID_KINDS: tuple[NoteKind, ...] = ("anotação", "pergunta", "recall")
DEFAULT_KIND: NoteKind = "anotação"

# "recall" is an English word spoken mid-Portuguese, so the Nemotron may render it
# several ways. Matching is plain text, so accepting the variants costs nothing and
# stops the override from failing on an accent. "recapitulando" is the escape hatch
# for when you would rather not depend on that at all.
RECALL_MARKERS: tuple[str, ...] = ("recall", "recal", "ricol", "recapitulando")

# Whole words only: "recalcular" contains "recal" and must not trigger the override.
_MARKER_RE = re.compile(
    r"\b(?:" + "|".join(RECALL_MARKERS) + r")\b", re.IGNORECASE | re.UNICODE
)


def spoken_recall_marker(transcript: str) -> bool:
    """True when the speaker said one of the marker words as a whole word."""
    return bool(_MARKER_RE.search(transcript or ""))


def resolve_kind(raw_transcript: str, inferred: str | None) -> NoteKind:
    """Combine the model's guess with the spoken override. The spoken word wins."""
    if spoken_recall_marker(raw_transcript):
        return "recall"
    if inferred in VALID_KINDS:
        return inferred  # type: ignore[return-value]
    return DEFAULT_KIND
