"""Tests for execution.core.agents_progress."""


def test_writer_phase_active():
    from execution.core.agents_progress import format_pipeline_progress
    text = format_pipeline_progress(current="Writer")
    assert "🖋️ *Writer* escrevendo... (1/3)" in text
    assert "⏳ Writer" in text
    assert "⏳ Reviewer" in text
    assert "⏳ Finalizer" in text


def test_reviewer_phase_active():
    from execution.core.agents_progress import format_pipeline_progress
    text = format_pipeline_progress(current="Reviewer", done=["Writer"])
    assert "🔍 *Reviewer* analisando... (2/3)" in text
    assert "✅ Writer" in text
    assert "⏳ Reviewer" in text
    assert "⏳ Finalizer" in text


def test_finalizer_phase_active():
    from execution.core.agents_progress import format_pipeline_progress
    text = format_pipeline_progress(current="Finalizer", done=["Writer", "Reviewer"])
    assert "✨ *Finalizer* polindo... (3/3)" in text
    assert "✅ Writer" in text
    assert "✅ Reviewer" in text
    assert "⏳ Finalizer" in text


def test_all_done():
    from execution.core.agents_progress import format_pipeline_progress
    text = format_pipeline_progress(current=None, done=["Writer", "Reviewer", "Finalizer"])
    assert "✅ *Draft pronto*" in text
    assert "✅ Writer" in text
    assert "✅ Reviewer" in text
    assert "✅ Finalizer" in text


def test_error_in_reviewer():
    from execution.core.agents_progress import format_pipeline_progress
    text = format_pipeline_progress(current="Reviewer", done=["Writer"], error="timeout após 60s")
    assert "❌ Erro em *Reviewer*" in text
    assert "✅ Writer" in text
    assert "❌ Reviewer" in text
    assert "⏸ Finalizer" in text
    assert "timeout após 60s" in text
