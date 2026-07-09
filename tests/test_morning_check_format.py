"""Layout 4c do morning_check (setas simplistas: ↑/↓/·)."""
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))


def test_morning_check_4c_lines():
    from execution.scripts.morning_check import build_message

    report_items = [
        {
            "variable_key": "IOBBA00",
            "product": "Brazilian Blend Fines",
            "price": 105.32,
            "change": 1.25,
            "changePercent": 1.20,
        },
        {
            "variable_key": "IODFE00",
            "product": "IO fines Fe 58%",
            "price": 90.10,
            "change": -0.85,
            "changePercent": -0.93,
        },
        {
            "variable_key": "IOPRM00",
            "product": "IO fines Fe 65%",
            "price": 120.00,
            "change": 0,
            "changePercent": 0,
        },
    ]

    msg = build_message(report_items, "09/07/2026")
    lines = msg.split("\n")

    assert lines[0] == "📊 *MINERALS TRADING DAILY REPORT*"
    assert lines[1] == "🔍 IRON ORE MARKET UPDATE - 09/07/2026"
    assert lines[2] == ""
    assert lines[3] == "🪨 *FINES*"
    assert lines[4] == "> *Brazilian Blend Fines*  `$105.32`  +1.25 (+1.20%) ↑"
    assert lines[5] == "> *IO fines Fe 58%*  `$90.10`  -0.85 (-0.93%) ↓"
    assert lines[6] == "> *IO fines Fe 65%*  `$120.00`  estável ·"
    assert len(lines) == 7

    # No other sections since only FINES_KEYS items were provided
    assert "LUMP AND PELLET" not in msg
    assert "VIU DIFFERENTIALS" not in msg
    assert "FREIGHT" not in msg
