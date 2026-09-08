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
