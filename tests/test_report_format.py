"""Primitivas de renderização compartilhadas pelos 3 crons do canal."""
import sys
from datetime import date
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))


def test_pill_date_pt_br_caps():
    from execution.core.report_format import pill_date
    assert pill_date(date(2026, 7, 30)) == "30/JUL"
    assert pill_date(date(2026, 1, 5)) == "05/JAN"
    assert pill_date(date(2026, 12, 31)) == "31/DEZ"


def test_pill_date_cobre_os_12_meses():
    from execution.core.report_format import pill_date
    esperado = ["JAN", "FEV", "MAR", "ABR", "MAI", "JUN",
                "JUL", "AGO", "SET", "OUT", "NOV", "DEZ"]
    for mes, sigla in enumerate(esperado, start=1):
        assert pill_date(date(2026, mes, 1)) == f"01/{sigla}"


def test_build_header_quatro_linhas_canonicas():
    from execution.core.report_format import build_header
    header = build_header("Baltic Exchange — Frete", "FRETE", date(2026, 7, 30))
    linhas = header.split("\n")
    assert linhas[0] == "📊 *MINERALS TRADING*"
    assert linhas[1] == "*Baltic Exchange — Frete*"
    assert linhas[2] == "`FRETE · 30/JUL`"
    assert linhas[3] == "─────────────────"
    assert len(linhas) == 4


def test_build_header_divisoria_unica():
    from execution.core.report_format import build_header, DIVIDER
    header = build_header("Qualquer", "ATIVO", date(2026, 7, 30))
    assert header.count(DIVIDER) == 1


def test_marker_for_tres_faixas():
    from execution.core.report_format import marker_for
    assert marker_for(1.5) == "↑"
    assert marker_for(-0.3) == "↓"
    assert marker_for(0) == "·"
    assert marker_for(0.0) == "·"


def test_format_row_sem_blockquote_e_sem_negrito_no_nome():
    from execution.core.report_format import format_row
    linha = format_row("Brazilian Blend Fines", "$107.90", "-0.55 (-0.51%)", "↓")
    assert linha == "Brazilian Blend Fines  `$107.90`  -0.55 (-0.51%) ↓"
    assert not linha.startswith("> ")      # blockquote tingia a linha toda
    assert "*Brazilian" not in linha       # nome fica sem negrito (Opção B)


def test_format_row_valor_e_o_unico_em_mono():
    from execution.core.report_format import format_row
    linha = format_row("BDI", "1842", "+23 (+1.26%)", "↑")
    assert linha.count("`") == 2


def test_translate_contract_month():
    from execution.core.report_format import translate_contract_month
    assert translate_contract_month("FEB/26") == "Fev/26"
    assert translate_contract_month("FEB26") == "Fev/26"
    assert translate_contract_month("aug/26") == "Ago/26"
    assert translate_contract_month("XYZ26") == "XYZ/26"  # desconhecido passa direto
