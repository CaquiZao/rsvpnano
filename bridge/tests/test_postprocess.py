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
