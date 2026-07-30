"""Layout do morning_check (header canônico + linha Opção B)."""
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))


def _itens():
    return [
        {"variable_key": "IOBBA00", "product": "Brazilian Blend Fines",
         "price": 105.32, "change": 1.25, "changePercent": 1.20},
        {"variable_key": "IODFE00", "product": "IO fines Fe 58%",
         "price": 90.10, "change": -0.85, "changePercent": -0.93},
        {"variable_key": "IOPRM00", "product": "IO fines Fe 65%",
         "price": 120.00, "change": 0, "changePercent": 0},
    ]


def test_morning_check_header_e_linhas():
    from execution.scripts.morning_check import build_message

    msg = build_message(_itens(), "09/07/2026")
    lines = msg.split("\n")

    assert lines[0] == "📊 *MINERALS TRADING*"
    assert lines[1] == "*Assessments Platts — Abertura*"
    assert lines[2] == "`IRON ORE · 09/JUL`"
    assert lines[3] == "─────────────────"
    assert lines[4] == ""
    assert lines[5] == "🪨 *FINES*"
    assert lines[6] == "Brazilian Blend Fines  `$105.32`  +1.25 (+1.20%) ↑"
    assert lines[7] == "IO fines Fe 58%  `$90.10`  -0.85 (-0.93%) ↓"
    assert lines[8] == "IO fines Fe 65%  `$120.00`  estável ·"
    assert len(lines) == 9

    # Só itens de FINES_KEYS foram passados
    assert "LUMP AND PELLET" not in msg
    assert "VIU DIFFERENTIALS" not in msg
    assert "FREIGHT" not in msg


def test_morning_check_mantem_emoji_de_secao_e_perde_blockquote():
    from execution.scripts.morning_check import build_message

    msg = build_message(_itens(), "09/07/2026")
    assert "🪨 *FINES*" in msg        # emoji de seção fica (decisão do usuário)
    assert "> " not in msg            # blockquote saiu
    assert msg.count("─────────────────") == 1


def test_morning_check_data_invalida_cai_pra_hoje():
    from datetime import datetime, timezone, timedelta
    from execution.scripts.morning_check import build_message
    from execution.core.report_format import pill_date

    msg = build_message(_itens(), "data-quebrada")
    hoje = datetime.now(timezone(timedelta(hours=-3))).date()
    assert f"`IRON ORE · {pill_date(hoje)}`" in msg
