import yaml

from handy_bridge import bases


def test_write_bases_creates_one_file_per_kind(tmp_path):
    written = bases.write_bases(tmp_path)
    assert {p.name for p in written} == {
        "Anotações.base",
        "Perguntas.base",
        "Recall.base",
    }
    assert all(p.is_file() for p in written)


def test_each_base_is_valid_yaml_with_a_table_view(tmp_path):
    # A sintaxe de filtro do Obsidian nao e verificavel aqui; YAML quebrado e.
    for path in bases.write_bases(tmp_path):
        parsed = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert parsed["views"][0]["type"] == "table"
        assert parsed["views"][0]["name"]


def test_each_base_filters_on_its_own_kind(tmp_path):
    for path in bases.write_bases(tmp_path):
        parsed = yaml.safe_load(path.read_text(encoding="utf-8"))
        conditions = parsed["filters"]["and"]
        kind = next(k for _, k, _ in bases.VIEWS if _base_name(k) == path.name)
        assert any(f'kind == "{kind}"' == str(c) for c in conditions)


def _base_name(kind: str) -> str:
    return next(name for name, k, _ in bases.VIEWS if k == kind)


def test_bases_sort_by_reading_position(tmp_path):
    parsed = yaml.safe_load(
        (bases.write_bases(tmp_path)[0]).read_text(encoding="utf-8")
    )
    props = [entry["property"] for entry in parsed["views"][0]["sort"]]
    # Livro, depois capitulo, depois offset: e a ordem em que o usuario leu.
    assert props == ["book", "chapter", "word_offset"]


def test_write_bases_is_idempotent(tmp_path):
    first = [p.read_text(encoding="utf-8") for p in bases.write_bases(tmp_path)]
    second = [p.read_text(encoding="utf-8") for p in bases.write_bases(tmp_path)]
    assert first == second


def test_write_bases_leaves_no_temp_files(tmp_path):
    bases.write_bases(tmp_path)
    assert [p.name for p in tmp_path.iterdir() if p.suffix != ".base"] == []
