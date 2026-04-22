"""Tests for execution.core.prompts — import + content checks."""


def test_writer_importable():
    from execution.core.prompts.writer import WRITER_SYSTEM
    assert isinstance(WRITER_SYSTEM, str)
    assert len(WRITER_SYSTEM) > 100


def test_writer_has_inviolable_rules():
    from execution.core.prompts.writer import WRITER_SYSTEM
    assert "jamais arredonde" in WRITER_SYSTEM.lower() or "nunca arredonde" in WRITER_SYSTEM.lower()
    assert "nunca invente" in WRITER_SYSTEM.lower() or "não invente" in WRITER_SYSTEM.lower()
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


def test_writer_has_trader_persona():
    from execution.core.prompts.writer import WRITER_SYSTEM
    lower = WRITER_SYSTEM.lower()
    assert "trader" in lower
    assert "mesa" in lower


def test_writer_has_drop_list():
    from execution.core.prompts.writer import WRITER_SYSTEM
    lower = WRITER_SYSTEM.lower()
    assert "sempre cortar" in lower or "o que cortar" in lower
    assert "platts is part of" in lower


def test_writer_has_budget():
    from execution.core.prompts.writer import WRITER_SYSTEM
    lower = WRITER_SYSTEM.lower()
    assert "1/3" in WRITER_SYSTEM or "um terço" in lower
    assert "18-22 linhas" in WRITER_SYSTEM


def test_writer_forbids_inventing():
    from execution.core.prompts.writer import WRITER_SYSTEM
    lower = WRITER_SYSTEM.lower()
    assert "nunca invente" in lower or "não invente" in lower


def test_writer_drops_tabular_phrase():
    from execution.core.prompts.writer import WRITER_SYSTEM
    assert "tabelas alinhadas" not in WRITER_SYSTEM


def test_writer_prefers_bullets():
    from execution.core.prompts.writer import WRITER_SYSTEM
    lower = WRITER_SYSTEM.lower()
    assert "bullet" in lower
    assert "prefira bullets" in lower or "bullets por default" in lower


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


def test_critique_checks_trader_voice():
    from execution.core.prompts.critique import CRITIQUE_SYSTEM
    lower = CRITIQUE_SYSTEM.lower()
    assert "trader" in lower or "robótic" in lower


def test_critique_checks_essence_not_completeness():
    from execution.core.prompts.critique import CRITIQUE_SYSTEM
    lower = CRITIQUE_SYSTEM.lower()
    assert "essência" in lower or "tese" in lower
    assert "dados completos" not in lower


def test_critique_checks_bloat():
    from execution.core.prompts.critique import CRITIQUE_SYSTEM
    lower = CRITIQUE_SYSTEM.lower()
    assert "inchado" in lower or "boilerplate" in lower or "repetição" in lower


def test_critique_checks_invention():
    from execution.core.prompts.critique import CRITIQUE_SYSTEM
    lower = CRITIQUE_SYSTEM.lower()
    assert "invenção" in lower or "invent" in lower


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


def test_curator_has_five_templates():
    """v4: Curator routes by [TIPO: ...] into 5 templates."""
    from execution.core.prompts.curator import CURATOR_SYSTEM
    assert "PRICING_SESSION" in CURATOR_SYSTEM
    assert "FUTURES_CURVE" in CURATOR_SYSTEM
    assert "COMPANY_NEWS" in CURATOR_SYSTEM
    assert "ANALYTICAL" in CURATOR_SYSTEM
    assert "DIGEST" in CURATOR_SYSTEM
    assert "EVENTO_CRITICO" not in CURATOR_SYSTEM


def test_curator_has_date_rule():
    """v4: Curator has explicit PT-BR date formatting rule."""
    from execution.core.prompts.curator import CURATOR_SYSTEM
    # Month abbreviations in PT-BR CAPS
    assert "ABR" in CURATOR_SYSTEM
    assert "MAI" in CURATOR_SYSTEM
    assert "DEZ" in CURATOR_SYSTEM
    # Data rule section exists
    lower = CURATOR_SYSTEM.lower()
    assert "data" in lower and "pt-br" in lower


def test_curator_has_ativo_dominante_rule():
    """v4: COMPANY_NEWS ativo dominante rule."""
    from execution.core.prompts.curator import CURATOR_SYSTEM
    lower = CURATOR_SYSTEM.lower()
    assert "ativo dominante" in lower


def test_curator_has_watch_render_rule():
    """v4: Watch: line rendered as plain prose (no bold/mono)."""
    from execution.core.prompts.curator import CURATOR_SYSTEM
    assert "Watch:" in CURATOR_SYSTEM


def test_curator_has_blank_line_rule():
    """v4: Blank-line-between-bullets rule (heterogeneous vs homogeneous)."""
    from execution.core.prompts.curator import CURATOR_SYSTEM
    lower = CURATOR_SYSTEM.lower()
    assert "heterogên" in lower or "homogên" in lower


def test_curator_has_proibido_list():
    """v4: Curator has proibido list (mirror of Writer)."""
    from execution.core.prompts.curator import CURATOR_SYSTEM
    lower = CURATOR_SYSTEM.lower()
    assert "significativo" in lower
    assert "dinâmica observada" in lower


def test_curator_has_hard_ceiling_per_type():
    """v4: Hard ceiling is per-type (not single 25-line rule)."""
    from execution.core.prompts.curator import CURATOR_SYSTEM
    assert "TETO" in CURATOR_SYSTEM or "teto" in CURATOR_SYSTEM
    # Per-type ceilings present
    assert "30" in CURATOR_SYSTEM  # PRICING_SESSION / COMPANY_NEWS
    assert "25" in CURATOR_SYSTEM  # others


def test_curator_has_futures_example_fixed():
    """v4: FUTURES example uses correct spread values from input."""
    from execution.core.prompts.curator import CURATOR_SYSTEM
    # Correct values from the SGX input
    assert "$0,60" in CURATOR_SYSTEM or "`$0,60`" in CURATOR_SYSTEM
    # Wrong v3 values must be gone
    assert "$0,40-0,45" not in CURATOR_SYSTEM
    assert "$1,50-1,60" not in CURATOR_SYSTEM


def test_curator_has_tabular_data_rule():
    from execution.core.prompts.curator import CURATOR_SYSTEM
    lower = CURATOR_SYSTEM.lower()
    assert "mono inline" in lower or "inline mono" in lower


def test_curator_has_few_shot_examples():
    """v4: 5 examples (one per type)."""
    from execution.core.prompts.curator import CURATOR_SYSTEM
    assert CURATOR_SYSTEM.count("EXEMPLO") >= 5
    assert "EXEMPLO 1" in CURATOR_SYSTEM
    assert "EXEMPLO 5" in CURATOR_SYSTEM


def test_curator_has_no_silencio_profissional():
    """v2: Removed redundant section."""
    from execution.core.prompts.curator import CURATOR_SYSTEM
    assert "SILÊNCIO PROFISSIONAL" not in CURATOR_SYSTEM


def test_curator_removes_source_footer():
    from execution.core.prompts.curator import CURATOR_SYSTEM
    lower = CURATOR_SYSTEM.lower()
    assert "platts is part of" in lower


def test_adjuster_importable():
    from execution.core.prompts.adjuster import ADJUSTER_SYSTEM
    assert isinstance(ADJUSTER_SYSTEM, str)
    assert len(ADJUSTER_SYSTEM) > 50


def test_adjuster_preserves_tables():
    from execution.core.prompts.adjuster import ADJUSTER_SYSTEM
    lower = ADJUSTER_SYSTEM.lower()
    assert "tabela" in lower


def test_adjuster_preserves_header():
    from execution.core.prompts.adjuster import ADJUSTER_SYSTEM
    assert "📊" in ADJUSTER_SYSTEM
    assert "MINERALS TRADING" in ADJUSTER_SYSTEM


def test_all_prompts_importable_from_package():
    """Verify __init__.py re-exports all 4 constants."""
    from execution.core.prompts import (
        WRITER_SYSTEM, CRITIQUE_SYSTEM, CURATOR_SYSTEM, ADJUSTER_SYSTEM
    )
    assert all(isinstance(p, str) and len(p) > 50 for p in [
        WRITER_SYSTEM, CRITIQUE_SYSTEM, CURATOR_SYSTEM, ADJUSTER_SYSTEM
    ])
