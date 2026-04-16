"""Tests for execution.core.prompts — import + content checks."""


def test_writer_importable():
    from execution.core.prompts.writer import WRITER_SYSTEM
    assert isinstance(WRITER_SYSTEM, str)
    assert len(WRITER_SYSTEM) > 100


def test_writer_has_inviolable_rules():
    from execution.core.prompts.writer import WRITER_SYSTEM
    assert "jamais arredonde" in WRITER_SYSTEM.lower() or "nunca arredonde" in WRITER_SYSTEM.lower()
    assert "interpretações pessoais" in WRITER_SYSTEM.lower() or "interpretação pessoal" in WRITER_SYSTEM.lower()
    assert "CFR" in WRITER_SYSTEM
    assert "FOB" in WRITER_SYSTEM
    assert "DATA NÃO ESPECIFICADA" in WRITER_SYSTEM


def test_writer_has_no_classification_tags():
    """v2: Writer output must NOT include [CLASSIFICAÇÃO] or [ELEMENTOS] tags."""
    from execution.core.prompts.writer import WRITER_SYSTEM
    assert "[CLASSIFICAÇÃO" not in WRITER_SYSTEM
    assert "[ELEMENTOS PRESENTES" not in WRITER_SYSTEM
    assert "[IMPACTO PRINCIPAL" not in WRITER_SYSTEM


def test_writer_has_few_shot_examples():
    from execution.core.prompts.writer import WRITER_SYSTEM
    assert "<example>" in WRITER_SYSTEM or "EXEMPLO" in WRITER_SYSTEM


def test_writer_has_tabular_data_rule():
    from execution.core.prompts.writer import WRITER_SYSTEM
    lower = WRITER_SYSTEM.lower()
    assert "tabela" in lower or "tabular" in lower
