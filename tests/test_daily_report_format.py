"""Layout do relatório diário SGX (header canônico + linha Opção B)."""
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))


def test_format_price_message_header_e_linhas():
    from execution.scripts.send_daily_report import format_price_message
    prices = [
        {"month": "AUG/26", "price": 103.50, "change": 0.55, "pct_change": 0.53},
        {"month": "OCT/26", "price": 102.60, "change": -0.15, "pct_change": -0.15},
        {"month": "NOV/26", "price": 102.10, "change": 0.00, "pct_change": 0.00},
    ]
    msg = format_price_message(prices)
    lines = msg.split("\n")

    assert lines[0] == "📊 *MINERALS TRADING*"
    assert lines[1] == "*Curva SGX — Iron Ore 62% Fe*"
    assert lines[2].startswith("`IRON ORE FUTURES · ")
    assert lines[2].endswith("`")
    assert lines[3] == "─────────────────"
    assert lines[4] == ""
    assert lines[5] == "Ago/26  `$103.50`  +0.55 (+0.53%) ↑"
    assert lines[6] == "Out/26  `$102.60`  -0.15 (-0.15%) ↓"
    assert lines[7] == "Nov/26  `$102.10`  estável ·"
    assert len(lines) == 8


def test_format_price_message_sem_blockquote_nem_bolinhas():
    from execution.scripts.send_daily_report import format_price_message
    msg = format_price_message(
        [{"month": "AUG/26", "price": 103.50, "change": 0.55, "pct_change": 0.53}]
    )
    assert "> " not in msg          # blockquote saiu
    assert "🟢" not in msg          # bolinhas viraram setas
    assert "🔴" not in msg
    assert "▪️" not in msg
    assert msg.count("─────────────────") == 1   # divisória única
