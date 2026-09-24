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
    '"questions" são as perguntas que a pessoa fez em voz alta e que dá para '
    "responder em poucas frases, cada uma reescrita como pergunta direta. Liste "
    'vazio quando ela não perguntou nada. Isto é independente de "tasks": uma '
    "pergunta é algo a responder, uma pendência é algo a fazer.\n"
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
    "O trecho do livro, quando vier, diz apenas ONDE a pessoa estava lendo. Ele é "
    "contexto, NÃO é o limite do que você pode usar: responda com seu conhecimento "
    "geral. Se o trecho ajudar, ancore a resposta nele; se não tiver relação com a "
    "pergunta, ignore o trecho e responda de qualquer forma, mencionando em uma "
    "frase que a dúvida não vem do trecho. "
    "NUNCA recuse uma pergunta por ela não estar no trecho — a pessoa gravou uma "
    "pergunta e uma recusa não é resposta.\n"
    "Responda cada pergunta individualmente. Duas respostas idênticas para "
    "perguntas diferentes significam que você não respondeu nenhuma.\n"
)

RECALL_PROMPT = (
    "Alguém está lendo um livro, parou e falou em voz alta o que lembrou e o que pensou. "
    "Sua tarefa tem TRÊS PARTES, com REGRAS DE CONHECIMENTO DIFERENTES. Não misture as "
    "regras entre as partes.\n"
    "Responda APENAS com um objeto JSON válido, sem cercas de código, no formato "
    '{"points": array de {"said": string, "actual": string, "correct": boolean}, '
    '"missed": array de strings, "reasoning": string, "deepening": string, '
    '"outside_passage": boolean}.\n'
    "\n"
    "PARTE 1 - CONFERENCIA DA MEMORIA (\"points\" e \"missed\").\n"
    'Uma entrada em "points" por afirmação factual que a pessoa fez sobre o texto. "said" '
    'resume a afirmação em uma frase curta. "correct" é true quando ela bate com o trecho. '
    'Quando é false, "actual" diz em uma frase o que o trecho de fato afirma; quando é true, '
    'deixe "actual" vazio. "missed" lista no máximo três coisas importantes do trecho que a '
    "pessoa não mencionou.\n"
    "AQUI, JULGUE SOMENTE CONTRA O TRECHO FORNECIDO, nunca contra conhecimento externo. Se o "
    "trecho não permite decidir, trate a afirmação como correta. Acusar erro que não houve é "
    "pior que deixar passar, porque é o que faz a pessoa parar de confiar na conferência.\n"
    "\n"
    "PARTE 2 - AVALIACAO DO RACIOCINIO (\"reasoning\").\n"
    "Avalie o PROCESSO DE PENSAMENTO, não a memória: a inferência se sustenta? onde ela "
    "escorrega, e POR QUÊ? Quando a pessoa errou, diga primeiro o que o instinto dela "
    "acertou e só depois onde ele falhou - quase sempre há um acerto dentro do erro, e é ele "
    "que faz a correção grudar. Se ela confundiu duas coisas parecidas, nomeie a distinção "
    "que resolve a confusão. Escreva em segunda pessoa. No máximo 130 palavras.\n"
    "\n"
    "PARTE 3 - APROFUNDAMENTO (\"deepening\").\n"
    "Estenda o que a pessoa falou: de três a cinco frases com o que ela ficaria feliz de "
    "saber em seguida, no fio que ela mesma puxou. AQUI conhecimento de mundo é permitido e "
    "esperado - é o que torna esta parte útil. Não repita a Parte 2 nem a conferência. "
    "Escreva em segunda pessoa. No máximo 130 palavras.\n"
    "\n"
    'QUANDO O TRECHO NAO COBRE O ASSUNTO: ponha "outside_passage" como true e deixe "points" '
    'e "missed" VAZIOS. Isso acontece quando a pessoa recorda algo que leu antes, ou a '
    "moldura geral do livro, e não o trecho atual. Conferir memória contra um texto que não "
    "trata do assunto só produz acusação falsa. As Partes 2 e 3 continuam valendo "
    "normalmente: é justamente aí que elas passam a ser o valor inteiro da conferência.\n"
)

CHAPTER_SUMMARY_PROMPT = (
    "Abaixo está tudo o que você registrou por voz enquanto lia um capítulo: "
    "anotações, perguntas com as respostas que recebeu, e conferências do que "
    "lembrou. Escreva uma síntese do capítulo.\n"
    "Responda APENAS com um objeto JSON válido, sem cercas de código, no formato "
    '{"synthesis": string}.\n'
    "A síntese deve ter NO MÁXIMO 350 palavras, ser organizada e fácil de absorver, "
    "e usar SOMENTE o que está nos registros abaixo. Não acrescente conhecimento "
    "sobre o livro nem preencha lacunas: o valor da síntese é ser o espelho do que "
    "quem gravou de fato entendeu, com as lacunas que isso tiver.\n"
    "ESCREVA EM SEGUNDA PESSOA, falando com quem gravou: \"você entendeu\", "
    "\"você perguntou\". NUNCA na terceira pessoa (\"a pessoa\", \"ela\") — é o "
    "caderno de quem gravou, não um relatório sobre ela.\n"
    "Quando uma conferência mostrar que você entendeu algo errado, registre as duas "
    "versões — o que você disse e o que o livro afirma — em vez de só a correta.\n"
)

BOOK_SUMMARY_PROMPT = (
    "Abaixo estão as sínteses dos capítulos de um livro, escritas a partir do que "
    "você registrou enquanto lia. Extraia os pontos mais importantes do livro como "
    "um todo, em segunda pessoa e nunca na terceira.\n"
    "Responda APENAS com um objeto JSON válido, sem cercas de código, no formato "
    '{"bullets": array de strings}.\n'
    "De 5 a 10 itens, cada um em uma frase, ordenados do mais para o menos "
    "importante. Julgue a importância olhando o conjunto: se um capítulo posterior "
    "mostra que algo que parecia central era secundário, rebaixe. "
    "Use SOMENTE o que está nas sínteses.\n"
)

FOLLOWUP_PROMPT = (
    "Alguém está lendo um livro e registra notas de voz. Abaixo vem uma nota dele e, "
    "quando houver, as perguntas e respostas que já trocaram sobre ela. Responda à "
    "pergunta nova.\n"
    "Responda APENAS com um objeto JSON válido, sem cercas de código, no formato "
    '{"answer": string}.\n'
    "A pergunta pode ser duas coisas diferentes, e as duas são legítimas: pedir que "
    "você esclareça uma resposta anterior que ficou insuficiente, ou uma pergunta nova "
    "sobre a própria nota, feita horas ou dias depois. Não presuma que a pessoa está "
    "reclamando de algo: atenda o que ela perguntou.\n"
    "A resposta deve ter NO MÁXIMO 150 palavras, ser direta e atacar exatamente o que "
    "foi perguntado. Não repita o que já foi dito antes. Não comece com introduções. "
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
    """Run the CLI with the prompt (cmd[2]) on stdin instead of in argv.

    Windows caps a command line at 32767 characters. A recall carries the book
    passage and blows past that; CreateProcess then fails with WinError 206,
    which Python raises as FileNotFoundError, so the log blamed a missing CLI.
    `claude -p` with no prompt argument reads it from stdin.
    """
    prompt = cmd[2]
    argv = cmd[:2] + cmd[3:]
    return subprocess.run(
        argv,
        input=prompt,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=timeout,
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
            # `is not None` before str(): a null in the array would otherwise
            # become the literal string "None" and be answered as a question.
            questions=[
                str(item).strip()
                for item in (payload.get("questions") or [])
                if item is not None and str(item).strip()
            ],
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
        """Judge the recollection and the thinking behind it, in one call.

        Runs without a passage on purpose. Only Part 1 needs one; Parts 2 and 3
        judge the reasoning and extend it, and were written to use knowledge from
        outside the book. A recording that arrived with no reading anchor used to
        produce nothing at all here, so what reached the phone was the transcript
        and not one word about it.
        """
        if not spoken.strip():
            return RecallCheck()

        no_passage = not passage.strip()
        read = (
            "\nTrecho lido: NENHUM - esta gravação chegou sem âncora de leitura, então a "
            'PARTE 1 não se aplica: ponha "outside_passage" como true e deixe "points" e '
            '"missed" vazios. Faça as Partes 2 e 3 normalmente, que é todo o valor aqui.\n'
            if no_passage
            else f"\nTrecho lido:\n{passage}\n"
        )
        payload = self._run(
            RECALL_PROMPT + read + f"\nO que a pessoa disse:\n{spoken}\n"
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
        # Fora do trecho a conferencia factual nao tem contra o que julgar: o modelo
        # foi instruido a esvazia-la, e aqui isso e imposto em vez de pedido.
        # Sem trecho a regra e a mesma e nao depende do modelo obedecer: nao ha
        # contra o que conferir, entao a conferencia cai aqui de qualquer forma.
        outside = bool(payload.get("outside_passage", False)) or no_passage
        if outside:
            points, missed = [], []
        return RecallCheck(
            points=points,
            missed=missed[:3],
            reasoning=str(payload.get("reasoning", "")).strip(),
            deepening=str(payload.get("deepening", "")).strip(),
            outside_passage=outside,
            no_passage=no_passage,
        )

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
                # Uma entrada sem pergunta é a própria nota, semeada quando o aviso
                # de chegada foi enviado -- não uma resposta que alguém deu.
                if asked.strip():
                    parts.append(f"P: {asked}\nR: {replied}\n")
                else:
                    parts.append(f"A nota diz:\n{replied}\n")
        parts.append(f"\nNova pergunta:\n{question}\n")

        payload = self._run("".join(parts))
        answer = str(payload.get("answer", "")).strip()
        if not answer:
            raise PostProcessError("model output has no 'answer' field")
        return answer
