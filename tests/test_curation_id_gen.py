"""Tests for execution.curation.id_gen."""


def test_generate_id_deterministic():
    from execution.curation.id_gen import generate_id
    a = generate_id("Top News - Ferrous Metals", "China steel output lags 2025")
    b = generate_id("Top News - Ferrous Metals", "China steel output lags 2025")
    assert a == b


def test_generate_id_different_for_different_input():
    from execution.curation.id_gen import generate_id
    a = generate_id("Top News - Ferrous Metals", "Title A")
    b = generate_id("Top News - Ferrous Metals", "Title B")
    assert a != b


def test_generate_id_length_is_12():
    from execution.curation.id_gen import generate_id
    result = generate_id("source", "title")
    assert len(result) == 12
    assert all(c in "0123456789abcdef" for c in result)


def test_generate_id_handles_empty_strings():
    from execution.curation.id_gen import generate_id
    result = generate_id("", "")
    assert len(result) == 12
