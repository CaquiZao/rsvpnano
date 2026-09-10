"""A note classified as a question must never come back unanswered.

Written after a live run of a real recording produced exactly that: the model
labelled the note `pergunta`, extracted no task, and so nothing was answered and
nothing said why. Answering used to depend on the deliberately conservative task
extraction, which is a different question from "did they ask something".
"""

from handy_bridge.pipeline import _questions_to_answer
from handy_bridge.postprocess import Task
from handy_bridge import summaries


def test_the_questions_the_model_heard_are_answered():
    got = _questions_to_answer("pergunta", ["O que é entropia?"], [], "corpo")
    assert got == ["O que é entropia?"]


def test_an_answerable_task_is_still_answered():
    got = _questions_to_answer(
        "anotação", [], [Task("Entender o Big Bang", "keyword", True)], "corpo"
    )
    assert got == ["Entender o Big Bang"]


def test_a_task_that_is_not_answerable_is_left_to_the_board():
    got = _questions_to_answer(
        "anotação", [], [Task("Reler o capítulo 5", "keyword", False)], "corpo"
    )
    assert got == []


def test_the_two_sources_are_merged_without_duplicates():
    got = _questions_to_answer(
        "pergunta",
        ["O que é entropia?"],
        [Task("o que É Entropia?", "keyword", True), Task("E isso?", "keyword", True)],
        "corpo",
    )
    assert got == ["O que é entropia?", "E isso?"]


def test_a_question_note_with_nothing_extracted_falls_back_to_its_body():
    # O caso real: rotulada como pergunta, sem pergunta e sem pendencia. Uma
    # chamada a mais vale mais que uma pergunta que fica sem resposta sem motivo.
    got = _questions_to_answer("pergunta", [], [], "Qual a definição de física?")
    assert got == ["Qual a definição de física?"]


def test_the_fallback_is_only_for_question_notes():
    assert _questions_to_answer("anotação", [], [], "achei interessante") == []
    assert _questions_to_answer("recall", [], [], "o big bang foi assim") == []


def test_an_empty_body_produces_no_question():
    assert _questions_to_answer("pergunta", [], [], "   ") == []


def test_blank_questions_are_dropped():
    got = _questions_to_answer("pergunta", ["  ", "Real?"], [], "corpo")
    assert got == ["Real?"]


# --- o defeito 2: a nota nao aparecia em nenhuma secao ----------------------


def _note(kind: str, **over) -> summaries.NoteSummary:
    base = dict(
        stem="2026-09-10 0200 - t",
        kind=kind,
        title="t",
        body="o que eu falei",
        questions=[],
        recall=[],
        chapter=3,
        word_offset=10,
    )
    base.update(over)
    return summaries.NoteSummary(**base)


def test_an_unanswered_question_still_shows_up_in_its_section():
    # Era o defeito: kind errado para Anotacoes, sem callout para Perguntas, logo
    # invisivel nas tres secoes e visivel so na sintese.
    got = summaries.sections_from([_note("pergunta")])
    assert got["Perguntas e respostas"]
    assert "o que eu falei" in got["Perguntas e respostas"][0]


def test_an_annotation_keeps_its_body_even_with_a_question_answered():
    # Uma anotacao que tambem carrega pergunta nao pode perder o texto da anotacao.
    got = summaries.sections_from(
        [_note("anotação", questions=[("Q?", "A.")])]
    )
    assert any("o que eu falei" in item for item in got["Anotações"])
    assert got["Perguntas e respostas"]


def test_an_answered_question_does_not_print_its_body_twice():
    # O fallback do pipeline pode fazer a pergunta *ser* o corpo.
    got = summaries.sections_from(
        [_note("pergunta", questions=[("o que eu falei", "A.")])]
    )
    assert len(got["Perguntas e respostas"]) == 1


def test_a_recall_keeps_its_body_alongside_the_check():
    got = summaries.sections_from([_note("recall", recall=["✓ **Você disse:** X"])])
    assert len(got["Recall"]) == 2


def test_an_unknown_kind_lands_in_annotations():
    got = summaries.sections_from([_note("reflexão")])
    assert got["Anotações"]


# --- o defeito 3: recusar por a pergunta nao estar no trecho -----------------


def test_the_answer_prompt_forbids_refusing_over_the_excerpt():
    from handy_bridge.postprocess.claude_cli import ANSWER_PROMPT

    # Rodada real: as quatro perguntas voltaram com a mesma recusa, porque o
    # trecho era lido como o limite do que se podia usar para responder.
    assert "NÃO é o limite" in ANSWER_PROMPT
    assert "NUNCA recuse" in ANSWER_PROMPT
    assert "conhecimento geral" in ANSWER_PROMPT


def test_the_answer_prompt_asks_for_one_answer_per_question():
    from handy_bridge.postprocess.claude_cli import ANSWER_PROMPT

    assert "individualmente" in ANSWER_PROMPT
    assert "idênticas" in ANSWER_PROMPT
