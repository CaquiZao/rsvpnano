import pytest

from handy_bridge import kind as kind_mod


def test_marker_word_overrides_the_model():
    got = kind_mod.resolve_kind("recapitulando, o big bang foi uma expansao", "pergunta")
    assert got == "recall"


@pytest.mark.parametrize("spoken", ["recall", "recal", "ricol", "recapitulando"])
def test_every_phonetic_variant_triggers_the_override(spoken):
    assert kind_mod.resolve_kind(f"entao {spoken}: o que eu entendi foi", None) == "recall"


@pytest.mark.parametrize("spoken", ["Recall", "RECALL", "Recapitulando"])
def test_the_override_ignores_case(spoken):
    assert kind_mod.resolve_kind(f"{spoken} o capitulo", "anotação") == "recall"


@pytest.mark.parametrize(
    "transcript",
    [
        "preciso recalcular a media",
        "vou recarregar a pagina",
        "isso e um recallzinho",
    ],
)
def test_marker_must_be_a_whole_word(transcript):
    # "recalcular" contem "recal" e nao pode disparar o override.
    assert kind_mod.resolve_kind(transcript, "anotação") == "anotação"


def test_the_marker_counts_anywhere_in_the_sentence():
    # A pessoa fala corrido; exigir a palavra no comeco tornaria o override inutil.
    got = kind_mod.resolve_kind("deixa eu ver se eu entendi, recall do capitulo", None)
    assert got == "recall"


def test_falls_back_to_the_model_when_no_marker_is_spoken():
    assert kind_mod.resolve_kind("o que e entropia?", "pergunta") == "pergunta"
    assert kind_mod.resolve_kind("achei o livro chato", "anotação") == "anotação"
    assert kind_mod.resolve_kind("o big bang foi assim", "recall") == "recall"


def test_unknown_model_value_degrades_to_annotation():
    # Um valor inventado pelo LLM nunca deve virar kind invalido no frontmatter.
    assert kind_mod.resolve_kind("texto", "reflexão") == "anotação"
    assert kind_mod.resolve_kind("texto", None) == "anotação"
    assert kind_mod.resolve_kind("texto", "") == "anotação"


def test_an_empty_transcript_is_an_annotation():
    assert kind_mod.resolve_kind("", None) == "anotação"


def test_every_valid_kind_is_accepted_from_the_model():
    for value in kind_mod.VALID_KINDS:
        assert kind_mod.resolve_kind("texto neutro", value) == value
