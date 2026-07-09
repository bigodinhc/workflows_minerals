"""Layout do relatório diário SGX (variante 4c aprovada no canal)."""
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))


def test_format_price_message_4c_layout():
    from execution.scripts.send_daily_report import format_price_message
    prices = [
        {"month": "AUG/26", "price": 103.50, "change": 0.55, "pct_change": 0.53},
        {"month": "OCT/26", "price": 102.60, "change": -0.15, "pct_change": -0.15},
        {"month": "NOV/26", "price": 102.10, "change": 0.00, "pct_change": 0.00},
    ]
    msg = format_price_message(prices)
    lines = msg.split("\n")
    assert lines[0] == "📊 *MINERALS TRADING DAILY REPORT*"
    assert lines[1].startswith("📈 SGX IRON ORE 62% FE FUTURES - ")
    assert lines[2] == "> *Ago/26*  `$103.50`  +0.55 (+0.53%) 🟢"
    assert lines[3] == "> *Out/26*  `$102.60`  -0.15 (-0.15%) 🔴"
    assert lines[4] == "> *Nov/26*  `$102.10`  estável ▪️"
    assert len(lines) == 5
