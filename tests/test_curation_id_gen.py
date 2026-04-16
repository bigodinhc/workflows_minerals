"""Tests for execution.curation.id_gen (v2: title-only canonical ID)."""
import pytest


def test_normalize_title_strips_whitespace():
    from execution.curation.id_gen import normalize_title
    assert normalize_title("  hello  ") == "hello"


def test_normalize_title_lowercases():
    from execution.curation.id_gen import normalize_title
    assert normalize_title("EU Steel Deal") == "eu steel deal"


def test_normalize_title_collapses_internal_whitespace():
    from execution.curation.id_gen import normalize_title
    assert normalize_title("china  steel   output") == "china steel output"


def test_normalize_title_normalizes_curly_quotes():
    from execution.curation.id_gen import normalize_title
    assert normalize_title("\u2018hello\u2019 \u201cworld\u201d") == "'hello' \"world\""


def test_normalize_title_strips_trailing_punctuation():
    from execution.curation.id_gen import normalize_title
    assert normalize_title("Title.") == "title"
    assert normalize_title("Title,") == "title"
    assert normalize_title("Title;") == "title"


def test_normalize_title_preserves_internal_punctuation():
    from execution.curation.id_gen import normalize_title
    assert normalize_title("U.S. steel tariffs") == "u.s. steel tariffs"


def test_normalize_title_raises_on_empty():
    from execution.curation.id_gen import normalize_title
    with pytest.raises(ValueError):
        normalize_title("")


def test_normalize_title_raises_on_whitespace_only():
    from execution.curation.id_gen import normalize_title
    with pytest.raises(ValueError):
        normalize_title("   ")


def test_normalize_title_raises_on_none():
    from execution.curation.id_gen import normalize_title
    with pytest.raises(ValueError):
        normalize_title(None)


def test_generate_id_deterministic():
    from execution.curation.id_gen import generate_id
    a = generate_id("China steel output lags 2025")
    b = generate_id("China steel output lags 2025")
    assert a == b


def test_generate_id_same_title_different_whitespace():
    from execution.curation.id_gen import generate_id
    a = generate_id("  China  steel  output ")
    b = generate_id("China steel output")
    assert a == b


def test_generate_id_same_title_different_case():
    from execution.curation.id_gen import generate_id
    a = generate_id("EU Steel Deal")
    b = generate_id("eu steel deal")
    assert a == b


def test_generate_id_different_titles():
    from execution.curation.id_gen import generate_id
    a = generate_id("Title A")
    b = generate_id("Title B")
    assert a != b


def test_generate_id_length_is_12_hex():
    from execution.curation.id_gen import generate_id
    result = generate_id("sample title")
    assert len(result) == 12
    assert all(c in "0123456789abcdef" for c in result)


def test_generate_id_raises_on_empty():
    from execution.curation.id_gen import generate_id
    with pytest.raises(ValueError):
        generate_id("")
