"""Post-process a transcript by shelling out to the `claude` CLI."""

from __future__ import annotations

import json
import subprocess
from typing import Callable

from handy_bridge.kind import resolve_kind
from handy_bridge.postprocess import (
    Answer,
    PostProcessError,
    PostProcessResult,
    RecallCheck,
    RecallPoint,
    Task,
)

MARKER_WORDS = ("pendência", "pendencia", "tarefa", "anotar")

PROMPT = (
    "Você recebe a transcrição bruta de uma nota de voz em português, gravada por "
    "alguém que estava lendo um livro. "
    "Responda APENAS com um objeto JSON válido, sem cercas de código, no formato "
    '{"title": string, "tags": array de strings, "cleaned": string, '
    '"kind": "anotação" ou "pergunta" ou "recall", '
    '"tasks": array de {"text": string, "source": "keyword" ou "inferred", '
    '"answerable": boolean}}.\n'
    '"title" é um título curto e descritivo. "tags" são de 2 a 5 tags em minúsculas. '
    '"cleaned" é a transcrição com pontuação corrigida e hesitações removidas, '
    "preservando o sentido e sem inventar informação.\n"
    '"kind" é a intenção da gravação. Use "recall" quando a pessoa está afirmando o '
    "que entendeu ou lembrou, para conferir se acertou. "
    'Use "pergunta" quando ela está pedindo explicação de algo que não entendeu. '
    'Use "anotação" para comentário, opinião ou registro solto. '
    'Em dúvida entre recall e anotação, escolha "anotação": afirmar o que se entendeu '
    "para ser corrigido é diferente de comentar o que se leu.\n"
    '"tasks" são pendências que a pessoa deixou. Use "keyword" quando ela disser '
    "explicitamente uma palavra marcadora (" + ", ".join(MARKER_WORDS) + ") seguida de "
    'uma ação. Use "inferred" quando não disser a palavra mas houver intenção clara de '
    "estudar, pesquisar ou revisar algo. "
    "Seja CONSERVADOR: se houver dúvida se é uma pendência ou apenas um comentário, "
    "OMITA. Um quadro com pendências inventadas é pior que um quadro incompleto.\n"
    '"answerable" é true apenas quando a pendência é uma dúvida conceitual que pode ser '
    "respondida de imediato em poucas frases, sem precisar de dado atual ou de trabalho "
    "da pessoa.\n\nTranscrição:\n"
)

ANSWER_PROMPT = (
    "Responda às perguntas abaixo, feitas por alguém que estava lendo um livro. "
    "Responda APENAS com um objeto JSON válido, sem cercas de código, no formato "
    '{"answers": array de {"question": string, "answer": string}}, repetindo cada '
    "pergunta exatamente como recebida.\n"
    "Cada resposta deve ter NO MÁXIMO 120 palavras, ser direta e concreta. "
    "Não comece com introduções como 'Ótima pergunta'. Não repita a pergunta na resposta. "
    "Se não souber com segurança, diga isso em uma frase em vez de especular.\n"
)

RECALL_PROMPT = (
    "Alguém está lendo um livro e acabou de falar em voz alta o que entendeu, para "
    "conferir se entendeu e se lembrou certo. Compare o que a pessoa disse com o "
    "trecho do livro que ela leu.\n"
    "Responda APENAS com um objeto JSON válido, sem cercas de código, no formato "
    '{"points": array de {"said": string, "actual": string, "correct": boolean}, '
    '"missed": array de strings}.\n'
    '"points" tem uma entrada por afirmação que a pessoa fez. "said" resume a '
    'afirmação dela em uma frase curta. "correct" é true quando a afirmação bate com '
    'o trecho. Quando "correct" é false, "actual" diz em uma frase o que o trecho '
    'de fato afirma; quando é true, deixe "actual" vazio.\n'
    '"missed" lista o que o trecho traz de importante e a pessoa não mencionou, no '
    "máximo três itens, cada um em uma frase curta.\n"
    "Julgue apenas contra o trecho fornecido, nunca contra conhecimento externo: se "
    "o trecho não permite decidir, trate a afirmação como correta. Acusar erro que "
    "não houve é pior que deixar passar, porque a pessoa para de confiar na "
    "conferência.\n"
)

CHAPTER_SUMMARY_PROMPT = (
    "Abaixo está tudo o que uma pessoa registrou por voz enquanto lia um capítulo: "
    "anotações, perguntas com as respostas que recebeu, e conferências do que ela "
    "lembrou. Escreva uma síntese do capítulo.\n"
    "Responda APENAS com um objeto JSON válido, sem cercas de código, no formato "
    '{"synthesis": string}.\n'
    "A síntese deve ter NO MÁXIMO 350 palavras, ser organizada e fácil de absorver, "
    "e usar SOMENTE o que está nos registros abaixo. Não acrescente conhecimento seu "
    "sobre o livro nem preencha lacunas: o valor da síntese é ser o espelho do que a "
    "pessoa de fato entendeu, com as lacunas que isso tiver.\n"
    "Quando uma conferência mostrar que a pessoa entendeu algo errado, registre as "
    "duas versões — o que ela disse e o que o livro afirma — em vez de só a correta.\n"
)

BOOK_SUMMARY_PROMPT = (
    "Abaixo estão as sínteses dos capítulos de um livro, escritas a partir do que "
    "uma pessoa registrou enquanto lia. Extraia os pontos mais importantes do livro "
    "como um todo.\n"
    "Responda APENAS com um objeto JSON válido, sem cercas de código, no formato "
    '{"bullets": array de strings}.\n'
    "De 5 a 10 itens, cada um em uma frase, ordenados do mais para o menos "
    "importante. Julgue a importância olhando o conjunto: se um capítulo posterior "
    "mostra que algo que parecia central era secundário, rebaixe. "
    "Use SOMENTE o que está nas sínteses.\n"
)

FOLLOWUP_PROMPT = (
    "Você está esclarecendo uma dúvida de alguém que está lendo um livro e que "
    "achou a resposta anterior insuficiente. "
    "Responda APENAS com um objeto JSON válido, sem cercas de código, no formato "
    '{"answer": string}.\n'
    "A resposta deve ter NO MÁXIMO 150 palavras, ser direta e atacar exatamente o que "
    "ficou obscuro. Não repita o que já foi dito antes. Não comece com introduções. "
    "Se não souber com segurança, diga isso em uma frase em vez de especular.\n"
)

_REQUIRED = ("title", "tags", "cleaned")
_VALID_SOURCES = ("keyword", "inferred")


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


def _parse_tasks(raw: object) -> list[Task]:
    """Read the tasks array defensively.

    Anything ambiguous degrades to "inferred" and answerable=False, so an
    uncertain extraction lands in the triage lane rather than being treated as
    something the speaker asked for outright.
    """
    if not isinstance(raw, list):
        return []

    tasks: list[Task] = []
    for entry in raw:
        if isinstance(entry, str):
            text, source, answerable = entry, "inferred", False
        elif isinstance(entry, dict):
            text = str(entry.get("text", ""))
            source = str(entry.get("source", "inferred"))
            answerable = bool(entry.get("answerable", False))
        else:
            continue

        text = text.strip()
        if not text:
            continue
        if source not in _VALID_SOURCES:
            source = "inferred"
        tasks.append(Task(text=text, source=source, answerable=answerable))
    return tasks


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

    def _run(self, prompt: str) -> dict:
        """Invoke the CLI and return the JSON object the model produced."""
        cmd = [
            "claude",
            "-p",
            prompt,
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

        return extract_json_object(str(wrapper.get("result", "")))

    def process(self, transcript: str) -> PostProcessResult:
        payload = self._run(PROMPT + transcript)
        missing = [key for key in _REQUIRED if key not in payload]
        if missing:
            raise PostProcessError(f"model output missing keys: {', '.join(missing)}")

        tags = [str(tag) for tag in payload["tags"] if tag is not None]
        return PostProcessResult(
            title=str(payload["title"]).strip(),
            tags=tags,
            cleaned=str(payload["cleaned"]).strip(),
            tasks=_parse_tasks(payload.get("tasks")),
            # The spoken marker word beats the model's inference, so the decision
            # goes through resolve_kind rather than straight into the result.
            kind=resolve_kind(transcript, str(payload.get("kind", "")) or None),
        )

    def answer_tasks(self, questions: list[str], excerpt: str | None) -> list[Answer]:
        """Answer every question in a single call.

        One call per question would multiply the ~30k tokens of Claude Code system
        prompt that each CLI invocation carries, so the batch matters for cost.
        """
        if not questions:
            return []

        parts = [ANSWER_PROMPT]
        if excerpt:
            parts.append(f"\nTrecho do livro que a pessoa estava lendo:\n{excerpt}\n")
        parts.append("\nPerguntas:\n")
        parts.extend(f"- {question}\n" for question in questions)

        payload = self._run("".join(parts))
        raw = payload.get("answers")
        if not isinstance(raw, list):
            raise PostProcessError("model output has no 'answers' list")

        answers: list[Answer] = []
        for entry in raw:
            if not isinstance(entry, dict):
                continue
            question = str(entry.get("question", "")).strip()
            answer = str(entry.get("answer", "")).strip()
            if question and answer:
                answers.append(Answer(question=question, answer=answer))
        return answers

    def check_recall(self, spoken: str, passage: str) -> RecallCheck:
        """Compare what was said against what was read, in one call."""
        if not spoken.strip() or not passage.strip():
            return RecallCheck()

        payload = self._run(
            RECALL_PROMPT
            + f"\nTrecho lido:\n{passage}\n"
            + f"\nO que a pessoa disse:\n{spoken}\n"
        )

        points: list[RecallPoint] = []
        for entry in payload.get("points") or []:
            if not isinstance(entry, dict):
                continue
            said = str(entry.get("said", "")).strip()
            if not said:
                continue
            # Anything ambiguous counts as correct: a false accusation costs more
            # than a miss, because it is what makes the check untrustworthy.
            correct = bool(entry.get("correct", True))
            actual = str(entry.get("actual", "")).strip()
            if not correct and not actual:
                correct = True
            points.append(RecallPoint(said=said, actual=actual, correct=correct))

        missed = [
            str(item).strip()
            for item in (payload.get("missed") or [])
            if str(item).strip()
        ]
        return RecallCheck(points=points, missed=missed[:3])

    def summarize_chapter(self, entries: list[str]) -> str:
        """Write the chapter synthesis from the recorded lines, in one call."""
        if not entries:
            return ""
        payload = self._run(
            CHAPTER_SUMMARY_PROMPT + "\nRegistros:\n" + "\n".join(f"- {e}" for e in entries)
        )
        return str(payload.get("synthesis", "")).strip()

    def summarize_book(self, syntheses: list[str]) -> list[str]:
        """Rank the book's most important points from the chapter syntheses."""
        if not syntheses:
            return []
        payload = self._run(
            BOOK_SUMMARY_PROMPT + "\nSínteses:\n" + "\n\n".join(syntheses)
        )
        return [
            str(item).strip()
            for item in (payload.get("bullets") or [])
            if str(item).strip()
        ]

    def answer_followup(
        self, question: str, history: list[tuple[str, str]], excerpt: str | None
    ) -> str:
        """Answer a Telegram follow-up, given the exchange so far."""
        parts = [FOLLOWUP_PROMPT]
        if excerpt:
            parts.append(f"\nTrecho do livro:\n{excerpt}\n")
        if history:
            parts.append("\nConversa até agora:\n")
            for asked, replied in history:
                parts.append(f"P: {asked}\nR: {replied}\n")
        parts.append(f"\nNova pergunta:\n{question}\n")

        payload = self._run("".join(parts))
        answer = str(payload.get("answer", "")).strip()
        if not answer:
            raise PostProcessError("model output has no 'answer' field")
        return answer
