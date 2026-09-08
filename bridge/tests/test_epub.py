import zipfile
from pathlib import Path

import pytest

from handy_bridge.epub import EpubError, convert_to_markdown, ensure_book_markdown

CONTAINER = """<?xml version="1.0"?>
<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container" version="1.0">
  <rootfiles><rootfile full-path="OEBPS/content.opf"
    media-type="application/oebps-package+xml"/></rootfiles>
</container>"""

OPF = """<?xml version="1.0"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0">
  <manifest>
    <item id="c2" href="zeta.xhtml" media-type="application/xhtml+xml"/>
    <item id="c1" href="alpha.xhtml" media-type="application/xhtml+xml"/>
    <item id="css" href="s.css" media-type="text/css"/>
  </manifest>
  <spine>
    <itemref idref="c2"/>
    <itemref idref="c1"/>
  </spine>
</package>"""

CH_ZETA = """<html xmlns="http://www.w3.org/1999/xhtml"><body>
  <h1>Primeiro Capitulo</h1><p>Texto do primeiro.</p>
  <style>p { color: red }</style>
</body></html>"""

CH_ALPHA = """<html xmlns="http://www.w3.org/1999/xhtml"><body>
  <h2>Segundo Capitulo</h2><p>Texto do <em>segundo</em>.</p>
</body></html>"""


def make_epub(path: Path) -> Path:
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("mimetype", "application/epub+zip")
        z.writestr("META-INF/container.xml", CONTAINER)
        z.writestr("OEBPS/content.opf", OPF)
        z.writestr("OEBPS/zeta.xhtml", CH_ZETA)
        z.writestr("OEBPS/alpha.xhtml", CH_ALPHA)
        z.writestr("OEBPS/s.css", "p{}")
    return path


def test_converts_and_respects_spine_order(tmp_path):
    out = convert_to_markdown(make_epub(tmp_path / "b.epub"), tmp_path / "b.md")
    text = out.read_text(encoding="utf-8")
    assert "# Primeiro Capitulo" in text
    assert "## Segundo Capitulo" in text
    # zeta vem antes de alpha porque o spine manda, nao a ordem alfabetica
    assert text.index("Primeiro Capitulo") < text.index("Segundo Capitulo")


def test_keeps_inline_text_and_drops_style_blocks(tmp_path):
    text = convert_to_markdown(
        make_epub(tmp_path / "b.epub"), tmp_path / "b.md"
    ).read_text(encoding="utf-8")
    assert "Texto do segundo." in text
    assert "color: red" not in text


def test_rejects_non_zip(tmp_path):
    bad = tmp_path / "bad.epub"
    bad.write_bytes(b"not a zip")
    with pytest.raises(EpubError, match="not a valid epub"):
        convert_to_markdown(bad, tmp_path / "out.md")


def test_rejects_epub_without_container(tmp_path):
    p = tmp_path / "empty.epub"
    with zipfile.ZipFile(p, "w") as z:
        z.writestr("mimetype", "application/epub+zip")
    with pytest.raises(EpubError, match="container.xml"):
        convert_to_markdown(p, tmp_path / "out.md")


def test_ensure_creates_markdown_under_subfolder(tmp_path):
    make_epub(tmp_path / "sapiens.epub")
    out = ensure_book_markdown(tmp_path, "sapiens")
    assert out is not None
    assert out == tmp_path / "Books" / "sapiens.md"
    assert "Primeiro Capitulo" in out.read_text(encoding="utf-8")


def test_ensure_is_idempotent(tmp_path):
    make_epub(tmp_path / "sapiens.epub")
    first = ensure_book_markdown(tmp_path, "sapiens")
    first.write_text("EDITADO A MAO", encoding="utf-8")
    second = ensure_book_markdown(tmp_path, "sapiens")
    # nao reconverte: o arquivo existente e preservado
    assert second.read_text(encoding="utf-8") == "EDITADO A MAO"


def test_ensure_returns_none_when_no_epub(tmp_path):
    assert ensure_book_markdown(tmp_path, "inexistente") is None
