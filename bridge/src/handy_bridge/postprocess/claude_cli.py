"""Post-process a transcript by shelling out to the `claude` CLI."""

from __future__ import annotations

import json
import subprocess
from typing import Callable

from handy_bridge.postprocess import PostProcessError, PostProcessResult

PROMPT = (
    "Você recebe a transcrição bruta de uma nota de voz em português. "
    "Responda APENAS com um objeto JSON válido, sem cercas de código, no formato "
    '{"title": string, "tags": array de strings, "cleaned": string}. '
    '"title" é um título curto e descritivo. "tags" são de 2 a 5 tags em minúsculas. '
    '"cleaned" é a transcrição com pontuação corrigida e hesitações removidas, '
    "preservando o sentido e sem inventar informação.\n\nTranscrição:\n"
)

_REQUIRED = ("title", "tags", "cleaned")


def extract_json_object(text: str) -> dict:
    """Pull the first balanced JSON object out of a string, ignoring code fences."""
    start = text.find("{")
    while start != -1:
        depth, in_str, esc = 0, False, False
        for idx in range(start, len(text)):
            ch = text[idx]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        parsed = json.loads(text[start : idx + 1])
                    except json.JSONDecodeError:
                        break
                    if isinstance(parsed, dict):
                        return parsed
                    break
        start = text.find("{", start + 1)
    raise PostProcessError("no JSON object found in model output")


def _default_runner(cmd: list[str], timeout: int) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd, capture_output=True, text=True, encoding="utf-8", timeout=timeout
    )


class ClaudeCliProcessor:
    def __init__(
        self,
        model: str,
        runner: Callable[
            [list[str], int], subprocess.CompletedProcess
        ] = _default_runner,
        timeout_s: int = 120,
    ):
        self._model = model
        self._runner = runner
        self._timeout_s = timeout_s

    def process(self, transcript: str) -> PostProcessResult:
        cmd = [
            "claude",
            "-p",
            PROMPT + transcript,
            "--output-format",
            "json",
            "--model",
            self._model,
        ]
        try:
            completed = self._runner(cmd, self._timeout_s)
        except subprocess.TimeoutExpired as exc:
            raise PostProcessError(
                f"claude CLI timed out after {self._timeout_s}s"
            ) from exc
        except FileNotFoundError as exc:
            raise PostProcessError("claude CLI not found on PATH") from exc

        if completed.returncode != 0:
            raise PostProcessError(
                f"claude CLI failed with exit code {completed.returncode}"
            )

        try:
            wrapper = json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            raise PostProcessError("claude CLI did not return JSON") from exc

        if wrapper.get("is_error"):
            raise PostProcessError(
                f"claude CLI reported an error: {wrapper.get('result')!r}"
            )

        payload = extract_json_object(str(wrapper.get("result", "")))
        missing = [key for key in _REQUIRED if key not in payload]
        if missing:
            raise PostProcessError(f"model output missing keys: {', '.join(missing)}")

        tags = [str(tag) for tag in payload["tags"] if tag is not None]
        return PostProcessResult(
            title=str(payload["title"]).strip(),
            tags=tags,
            cleaned=str(payload["cleaned"]).strip(),
        )
