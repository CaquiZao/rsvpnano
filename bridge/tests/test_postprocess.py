import json

import pytest

from handy_bridge.config import PostProcessConfig
from handy_bridge.postprocess import PostProcessError, PostProcessResult, build
from handy_bridge.postprocess.claude_cli import ClaudeCliProcessor, extract_json_object


class FakeCompleted:
    def __init__(self, stdout: str, returncode: int = 0):
        self.stdout, self.returncode, self.stderr = stdout, returncode, ""


def wrapper(result_text: str, is_error: bool = False) -> str:
    return json.dumps(
        {"result": result_text, "is_error": is_error, "subtype": "success"}
    )


def test_parses_wrapper_then_inner_json():
    inner = '{"title": "Captura por voz", "tags": ["ideia"], "cleaned": "Texto limpo."}'
    proc = ClaudeCliProcessor(
        "claude-haiku-4-5-20251001",
        runner=lambda cmd, timeout: FakeCompleted(wrapper(inner)),
    )
    out = proc.process("texto cru")
    assert out == PostProcessResult(
        title="Captura por voz", tags=["ideia"], cleaned="Texto limpo."
    )


def test_tolerates_code_fences_around_inner_json():
    inner = '```json\n{"title": "T", "tags": [], "cleaned": "C"}\n```'
    proc = ClaudeCliProcessor(
        "m", runner=lambda cmd, timeout: FakeCompleted(wrapper(inner))
    )
    assert proc.process("x").title == "T"


def test_passes_model_flag_and_transcript():
    seen = {}

    def runner(cmd, timeout):
        seen["cmd"] = cmd
        return FakeCompleted(wrapper('{"title":"T","tags":[],"cleaned":"C"}'))

    ClaudeCliProcessor("claude-haiku-4-5-20251001", runner=runner).process("minha fala")
    assert "--model" in seen["cmd"]
    assert "claude-haiku-4-5-20251001" in seen["cmd"]
    assert "--output-format" in seen["cmd"] and "json" in seen["cmd"]
    assert any("minha fala" in part for part in seen["cmd"])


def _processor(inner: str) -> ClaudeCliProcessor:
    return ClaudeCliProcessor(
        "m", runner=lambda cmd, timeout: FakeCompleted(wrapper(inner))
    )


def test_process_reads_the_kind_the_model_returned():
    inner = '{"title":"T","tags":[],"cleaned":"C","kind":"pergunta"}'
    assert _processor(inner).process("o que e entropia?").kind == "pergunta"


def test_process_applies_the_spoken_override_over_the_model():
    inner = '{"title":"T","tags":[],"cleaned":"C","kind":"pergunta"}'
    got = _processor(inner).process("recapitulando: o big bang foi uma expansao")
    assert got.kind == "recall"


def test_process_defaults_the_kind_when_the_model_omits_it():
    # Backends antigos e saidas incompletas nao podem produzir kind invalido.
    inner = '{"title":"T","tags":[],"cleaned":"C"}'
    assert _processor(inner).process("texto neutro").kind == "anotação"


def test_process_asks_the_model_for_a_kind():
    seen = {}

    def runner(cmd, timeout):
        seen["cmd"] = cmd
        return FakeCompleted(wrapper('{"title":"T","tags":[],"cleaned":"C"}'))

    ClaudeCliProcessor("m", runner=runner).process("x")
    prompt = next(part for part in seen["cmd"] if "kind" in part)
    assert '"kind"' in prompt and "recall" in prompt


def test_raises_when_cli_reports_error():
    proc = ClaudeCliProcessor(
        "m", runner=lambda cmd, timeout: FakeCompleted(wrapper("boom", True))
    )
    with pytest.raises(PostProcessError, match="reported an error"):
        proc.process("x")


def test_raises_on_nonzero_exit():
    proc = ClaudeCliProcessor("m", runner=lambda cmd, timeout: FakeCompleted("", 1))
    with pytest.raises(PostProcessError, match="exit code 1"):
        proc.process("x")


def test_raises_when_inner_json_is_not_an_object():
    proc = ClaudeCliProcessor(
        "m",
        runner=lambda cmd, timeout: FakeCompleted(wrapper("desculpe, nao consigo")),
    )
    with pytest.raises(PostProcessError, match="no JSON object"):
        proc.process("x")


def test_raises_when_required_key_missing():
    inner = '{"title": "T", "cleaned": "C"}'
    proc = ClaudeCliProcessor(
        "m", runner=lambda cmd, timeout: FakeCompleted(wrapper(inner))
    )
    with pytest.raises(PostProcessError, match="tags"):
        proc.process("x")


def test_coerces_tags_to_list_of_strings():
    inner = '{"title": "T", "tags": ["a", 2, null], "cleaned": "C"}'
    proc = ClaudeCliProcessor(
        "m", runner=lambda cmd, timeout: FakeCompleted(wrapper(inner))
    )
    assert proc.process("x").tags == ["a", "2"]


def test_extract_json_object_finds_embedded_object():
    assert extract_json_object('bla {"a": 1} bla') == {"a": 1}


def test_build_returns_none_when_disabled():
    assert build(PostProcessConfig(enabled=False, backend="claude_cli", model="m")) is None


def test_build_returns_none_for_none_backend():
    assert build(PostProcessConfig(enabled=True, backend="none", model="")) is None


def test_build_returns_claude_processor():
    got = build(PostProcessConfig(enabled=True, backend="claude_cli", model="m"))
    assert isinstance(got, ClaudeCliProcessor)


def test_build_rejects_unimplemented_backend():
    with pytest.raises(PostProcessError, match="not implemented"):
        build(PostProcessConfig(enabled=True, backend="ollama", model="m"))


# --- extracao de tarefas -------------------------------------------------


def _proc(inner: str) -> ClaudeCliProcessor:
    return ClaudeCliProcessor("m", runner=lambda cmd, timeout: FakeCompleted(wrapper(inner)))


def test_parses_tasks_with_source_and_answerable():
    inner = (
        '{"title":"T","tags":[],"cleaned":"C","tasks":['
        '{"text":"Pesquisar o Big Bang","source":"keyword","answerable":true},'
        '{"text":"Reler o capitulo 5","source":"inferred","answerable":false}]}'
    )
    tasks = _proc(inner).process("x").tasks
    assert len(tasks) == 2
    assert tasks[0].text == "Pesquisar o Big Bang"
    assert tasks[0].source == "keyword"
    assert tasks[0].answerable is True
    assert tasks[1].source == "inferred"
    assert tasks[1].answerable is False


def test_tasks_default_to_empty_when_the_model_omits_them():
    inner = '{"title":"T","tags":[],"cleaned":"C"}'
    assert _proc(inner).process("x").tasks == []


def test_unknown_source_falls_back_to_inferred():
    # Conservador: sem certeza de que o usuario foi explicito, vai para triagem.
    inner = '{"title":"T","tags":[],"cleaned":"C","tasks":[{"text":"a","source":"palpite"}]}'
    assert _proc(inner).process("x").tasks[0].source == "inferred"


def test_missing_answerable_defaults_to_false():
    inner = '{"title":"T","tags":[],"cleaned":"C","tasks":[{"text":"a","source":"keyword"}]}'
    assert _proc(inner).process("x").tasks[0].answerable is False


def test_tasks_without_text_are_dropped():
    inner = (
        '{"title":"T","tags":[],"cleaned":"C","tasks":['
        '{"text":"","source":"keyword"},{"text":"  ","source":"inferred"},'
        '{"source":"keyword"},{"text":"boa","source":"keyword"}]}'
    )
    tasks = _proc(inner).process("x").tasks
    assert [t.text for t in tasks] == ["boa"]


def test_tasks_tolerates_a_plain_string_list():
    # Alguns modelos devolvem só as strings; tratamos como inferidas.
    inner = '{"title":"T","tags":[],"cleaned":"C","tasks":["ler mais sobre isso"]}'
    tasks = _proc(inner).process("x").tasks
    assert tasks[0].text == "ler mais sobre isso"
    assert tasks[0].source == "inferred"


# --- respostas em lote ---------------------------------------------------


def test_answer_tasks_batches_everything_into_one_call():
    calls = []

    def runner(cmd, timeout):
        calls.append(cmd)
        return FakeCompleted(
            wrapper(
                '{"answers":[{"question":"O que foi o Big Bang?","answer":"O evento inicial."},'
                '{"question":"Quem foi Harari?","answer":"O autor."}]}'
            )
        )

    proc = ClaudeCliProcessor("m", runner=runner)
    answers = proc.answer_tasks(["O que foi o Big Bang?", "Quem foi Harari?"], excerpt="trecho")
    assert len(calls) == 1, "duas perguntas devem custar uma chamada, nao duas"
    assert answers[0].question == "O que foi o Big Bang?"
    assert answers[0].answer == "O evento inicial."
    assert answers[1].answer == "O autor."


def test_answer_tasks_sends_the_book_excerpt_as_context():
    seen = {}

    def runner(cmd, timeout):
        seen["cmd"] = cmd
        return FakeCompleted(wrapper('{"answers":[{"question":"q","answer":"a"}]}'))

    ClaudeCliProcessor("m", runner=runner).answer_tasks(["q"], excerpt="a Revolução Agrícola")
    assert any("a Revolução Agrícola" in part for part in seen["cmd"])


def test_answer_tasks_with_nothing_to_do_makes_no_call():
    def runner(cmd, timeout):
        raise AssertionError("nao deveria chamar o CLI")

    assert ClaudeCliProcessor("m", runner=runner).answer_tasks([], excerpt=None) == []


def test_answer_tasks_drops_entries_missing_a_field():
    inner = '{"answers":[{"question":"q"},{"answer":"sem pergunta"},{"question":"ok","answer":"boa"}]}'
    proc = ClaudeCliProcessor("m", runner=lambda cmd, timeout: FakeCompleted(wrapper(inner)))
    answers = proc.answer_tasks(["q", "ok"], excerpt=None)
    assert [(a.question, a.answer) for a in answers] == [("ok", "boa")]


def test_answer_tasks_propagates_a_cli_failure():
    proc = ClaudeCliProcessor("m", runner=lambda cmd, timeout: FakeCompleted("", 1))
    with pytest.raises(PostProcessError, match="exit code 1"):
        proc.answer_tasks(["q"], excerpt=None)


# --- acompanhamento com historico ----------------------------------------


def test_answer_followup_returns_a_single_answer():
    proc = ClaudeCliProcessor(
        "m", runner=lambda cmd, timeout: FakeCompleted(wrapper('{"answer":"Mais claro assim."}'))
    )
    assert proc.answer_followup("e isso?", history=[], excerpt=None) == "Mais claro assim."


def test_answer_followup_sends_the_previous_exchange():
    seen = {}

    def runner(cmd, timeout):
        seen["cmd"] = cmd
        return FakeCompleted(wrapper('{"answer":"ok"}'))

    ClaudeCliProcessor("m", runner=runner).answer_followup(
        "e a singularidade?",
        history=[("O que foi o Big Bang?", "O evento inicial.")],
        excerpt="trecho do livro",
    )
    joined = " ".join(seen["cmd"])
    assert "O que foi o Big Bang?" in joined
    assert "O evento inicial." in joined
    assert "trecho do livro" in joined
    assert "e a singularidade?" in joined


def test_answer_followup_rejects_output_without_an_answer():
    proc = ClaudeCliProcessor(
        "m", runner=lambda cmd, timeout: FakeCompleted(wrapper('{"outro":"campo"}'))
    )
    with pytest.raises(PostProcessError, match="answer"):
        proc.answer_followup("q", history=[], excerpt=None)


# --- conferencia de recall --------------------------------------------------


def test_check_recall_parses_points_and_missed():
    inner = (
        '{"points": [{"said":"A veio antes de B","actual":"","correct":true},'
        '{"said":"Atomos no primeiro segundo","actual":"Alguns minutos depois",'
        '"correct":false}], "missed": ["O trecho falava de Flores"]}'
    )
    got = _processor(inner).check_recall("o que eu entendi", "trecho do livro")
    assert [p.correct for p in got.points] == [True, False]
    assert got.points[1].actual == "Alguns minutos depois"
    assert got.missed == ["O trecho falava de Flores"]


def test_check_recall_treats_an_unsupported_accusation_as_correct():
    # Sem dizer o que o trecho de fato afirma, a acusacao nao se sustenta, e
    # acusar errado e o que faz a pessoa parar de confiar na conferencia.
    inner = '{"points": [{"said":"X","actual":"","correct":false}], "missed": []}'
    got = _processor(inner).check_recall("falei", "trecho")
    assert got.points[0].correct is True


def test_check_recall_caps_the_missed_list_at_three():
    inner = '{"points": [], "missed": ["a","b","c","d","e"]}'
    assert len(_processor(inner).check_recall("f", "t").missed) == 3


def test_check_recall_skips_the_call_without_a_passage():
    calls = []

    def runner(cmd, timeout):
        calls.append(cmd)
        return FakeCompleted(wrapper("{}"))

    proc = ClaudeCliProcessor("m", runner=runner)
    assert not proc.check_recall("falei bastante", "")
    assert not proc.check_recall("", "trecho do livro")
    # Nenhuma chamada gasta quando nao ha o que comparar.
    assert calls == []


def test_check_recall_drops_a_point_without_a_claim():
    inner = '{"points": [{"said":"  ","actual":"x","correct":false}], "missed": []}'
    assert _processor(inner).check_recall("f", "t").points == []


def test_check_recall_sends_both_the_passage_and_the_speech():
    seen = {}

    def runner(cmd, timeout):
        seen["prompt"] = cmd[2]
        return FakeCompleted(wrapper('{"points": [], "missed": []}'))

    ClaudeCliProcessor("m", runner=runner).check_recall("minha fala", "o trecho lido")
    assert "minha fala" in seen["prompt"]
    assert "o trecho lido" in seen["prompt"]


def test_process_reads_the_questions_separately_from_the_tasks():
    inner = (
        '{"title":"T","tags":[],"cleaned":"C","kind":"pergunta",'
        '"questions":["O que e entropia?"],"tasks":[]}'
    )
    got = _processor(inner).process("o que e entropia?")
    assert got.questions == ["O que e entropia?"]
    assert got.tasks == []


def test_questions_default_to_empty_when_the_model_omits_them():
    inner = '{"title":"T","tags":[],"cleaned":"C"}'
    assert _processor(inner).process("x").questions == []


def test_blank_questions_from_the_model_are_dropped():
    inner = '{"title":"T","tags":[],"cleaned":"C","questions":["  ",null,"Real?"]}'
    assert _processor(inner).process("x").questions == ["Real?"]


def test_the_prompt_asks_for_questions_apart_from_tasks():
    seen = {}

    def runner(cmd, timeout):
        seen["prompt"] = cmd[2]
        return FakeCompleted(wrapper('{"title":"T","tags":[],"cleaned":"C"}'))

    ClaudeCliProcessor("m", runner=runner).process("x")
    assert '"questions"' in seen["prompt"]
    assert "algo a responder" in seen["prompt"]


def test_the_chapter_summary_prompt_demands_second_person():
    from handy_bridge.postprocess.claude_cli import CHAPTER_SUMMARY_PROMPT

    # O resumo saiu falando "a pessoa" e "ela"; num caderno pessoal isso le errado.
    assert "SEGUNDA PESSOA" in CHAPTER_SUMMARY_PROMPT
    assert "NUNCA na terceira pessoa" in CHAPTER_SUMMARY_PROMPT


def test_check_recall_parses_the_reasoning_and_the_deepening():
    inner = (
        '{"points": [], "missed": [], "outside_passage": false,'
        ' "reasoning": "Seu instinto acertou a aceleracao, errou o inicio.",'
        ' "deepening": "A Belle Epoque foi o apice, nao a largada."}'
    )
    got = _processor(inner).check_recall("o que eu pensei", "trecho do livro")
    assert got.reasoning.startswith("Seu instinto acertou")
    assert "apice" in got.deepening
    assert got.outside_passage is False
    # A secao existe mesmo sem conferencia factual: e o que o usuario pediu.
    assert bool(got) is True


def test_check_recall_drops_the_factual_check_when_the_passage_does_not_cover_it():
    # Imposto no codigo, nao confiado ao modelo: conferir memoria contra um texto
    # que nao trata do assunto so produz acusacao falsa.
    inner = (
        '{"points": [{"said":"A revolucao cientifica foi em 1900","actual":"Foi ha 500 anos",'
        '"correct":false}], "missed": ["algo do trecho"], "outside_passage": true,'
        ' "reasoning": "Voce confundiu o inicio com o apice.", "deepening": "Mais contexto."}'
    )
    got = _processor(inner).check_recall("falei das tres revolucoes", "trecho sobre a Africa")
    assert got.outside_passage is True
    assert got.points == []
    assert got.missed == []
    assert got.reasoning
    assert got.deepening
