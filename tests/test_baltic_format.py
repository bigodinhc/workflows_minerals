"""Layout do baltic_ingestion (header canônico + linha Opção B + setas)."""
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))


def _dados():
    return {
        "report_date": "2026-07-09",
        "bdi": {"value": 1850, "change": 25},
        "capesize": {"value": 2100, "change": -50},
        "panamax": {"value": 1200, "change": 0},
        "supramax": {"value": 800, "change": 10},
        "handysize": {"value": 500, "change": -5},
        "routes": [
            {"code": "C3", "value": 25.50, "change": -0.30},
            {"code": "C5", "value": 10.20, "change": 0},
            {"code": "C5TC", "value": 15000, "change": 200},
        ],
    }


def test_baltic_header_e_linhas():
    from execution.scripts.baltic_ingestion import format_whatsapp_message

    msg = format_whatsapp_message(_dados())
    lines = msg.split("\n")

    assert lines[0] == "📊 *MINERALS TRADING*"
    assert lines[1] == "*Baltic Exchange — Frete*"
    assert lines[2] == "`FRETE · 09/JUL`"
    assert lines[3] == "─────────────────"
    assert lines[4] == ""
    assert lines[5] == "🌊 *BALTIC DRY INDEX*"
    assert lines[6] == "BDI  `1850`  +25 (+1.37%) ↑"
    assert lines[7] == ""
    assert lines[8] == "⚓ *ROTAS CAPESIZE*"
    assert lines[9] == "C3 Tubarao → Qingdao  `$25.50/ton`  -0.30 (-1.16%) ↓"
    assert lines[10] == "C5 W.Australia → Qingdao  `$10.20/ton`  estável ·"
    assert lines[11] == "C5TC Timecharter Avg  `15000/day`  +200 (+1.35%) ↑"
    assert lines[12] == ""
    assert lines[13] == "🚢 *INDICES POR TIPO*"
    assert lines[14] == "Capesize (100k+ DWT)  `2100`  -50 (-2.33%) ↓"
    assert lines[15] == "Panamax (60-80k DWT)  `1200`  estável ·"
    assert lines[16] == "Supramax (45-60k DWT)  `800`  +10 (+1.27%) ↑"
    assert lines[17] == "Handysize (15-35k DWT)  `500`  -5 (-0.99%) ↓"
    assert len(lines) == 18

    # Rotas C2/C7 ausentes do input → linhas puladas
    assert "C2 Tubarao" not in msg
    assert "C7 Bolivar" not in msg


def test_baltic_sem_blockquote_nem_bolinhas():
    from execution.scripts.baltic_ingestion import format_whatsapp_message

    msg = format_whatsapp_message(_dados())
    assert "> " not in msg
    assert "🟢" not in msg
    assert "🔴" not in msg
    assert "▪️" not in msg
    assert msg.count("─────────────────") == 1


def test_baltic_data_invalida_cai_pra_hoje():
    from datetime import datetime, timezone, timedelta
    from execution.scripts.baltic_ingestion import format_whatsapp_message
    from execution.core.report_format import pill_date

    dados = {**_dados(), "report_date": ""}
    msg = format_whatsapp_message(dados)
    hoje = datetime.now(timezone(timedelta(hours=-3))).date()
    assert f"`FRETE · {pill_date(hoje)}`" in msg
