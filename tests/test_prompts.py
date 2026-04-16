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


def test_critique_importable():
    from execution.core.prompts.critique import CRITIQUE_SYSTEM
    assert isinstance(CRITIQUE_SYSTEM, str)
    assert len(CRITIQUE_SYSTEM) > 100


def test_critique_is_concise():
    """v2: Critique should be under 2000 chars (was ~5000 in v1)."""
    from execution.core.prompts.critique import CRITIQUE_SYSTEM
    assert len(CRITIQUE_SYSTEM) < 2000


def test_critique_has_no_praise_section():
    """v2: No PONTOS DE EXCELÊNCIA — critique only corrects."""
    from execution.core.prompts.critique import CRITIQUE_SYSTEM
    assert "EXCELÊNCIA" not in CRITIQUE_SYSTEM
    assert "OTIMIZAÇÕES OPCIONAIS" not in CRITIQUE_SYSTEM


def test_critique_checks_tabular_data():
    from execution.core.prompts.critique import CRITIQUE_SYSTEM
    lower = CRITIQUE_SYSTEM.lower()
    assert "tabela" in lower or "tabular" in lower


def test_critique_checks_trader_voice():
    from execution.core.prompts.critique import CRITIQUE_SYSTEM
    lower = CRITIQUE_SYSTEM.lower()
    assert "trader" in lower or "robótic" in lower


def test_curator_importable():
    from execution.core.prompts.curator import CURATOR_SYSTEM
    assert isinstance(CURATOR_SYSTEM, str)
    assert len(CURATOR_SYSTEM) > 100


def test_curator_has_header_rules():
    from execution.core.prompts.curator import CURATOR_SYSTEM
    assert "📊" in CURATOR_SYSTEM
    assert "MINERALS TRADING" in CURATOR_SYSTEM
    assert "─────────────────" in CURATOR_SYSTEM


def test_curator_has_whatsapp_format_rules():
    from execution.core.prompts.curator import CURATOR_SYSTEM
    assert "*texto*" in CURATOR_SYSTEM or "*negrito*" in CURATOR_SYSTEM
    assert "###" in CURATOR_SYSTEM  # in PROIBIDO section


def test_curator_has_tabular_data_rule():
    from execution.core.prompts.curator import CURATOR_SYSTEM
    lower = CURATOR_SYSTEM.lower()
    assert "tabela" in lower
    assert "prosa" in lower


def test_curator_has_few_shot_examples():
    from execution.core.prompts.curator import CURATOR_SYSTEM
    assert "EXEMPLO" in CURATOR_SYSTEM


def test_curator_has_no_silencio_profissional():
    """v2: Removed redundant section."""
    from execution.core.prompts.curator import CURATOR_SYSTEM
    assert "SILÊNCIO PROFISSIONAL" not in CURATOR_SYSTEM
