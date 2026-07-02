from app.knowledge.parser import parse_document


def test_parse_txt(tmp_path):
    f = tmp_path / "note.txt"
    f.write_text("hello 外贸", encoding="utf-8")
    assert parse_document(f) == "hello 外贸"


def test_parse_html(tmp_path):
    f = tmp_path / "page.html"
    f.write_text("<p>product <b>spec</b></p>", encoding="utf-8")
    assert "product spec" in parse_document(f)


def test_parse_unsupported(tmp_path):
    f = tmp_path / "x.xyz"
    f.write_text("x", encoding="utf-8")
    import pytest
    with pytest.raises(ValueError):
        parse_document(f)
