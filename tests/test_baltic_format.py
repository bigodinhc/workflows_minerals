"""Layout 4c do baltic_ingestion (bolinhas: 🟢/🔴/▪️)."""
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))


def test_baltic_4c_lines():
    from execution.scripts.baltic_ingestion import format_whatsapp_message

    data = {
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

    msg = format_whatsapp_message(data)
    lines = msg.split("\n")

    assert lines[0] == "📊 *MINERALS TRADING DAILY REPORT*"
    assert lines[1] == "🚢 BALTIC EXCHANGE UPDATE - 09/07/2026"
    assert lines[2] == ""
    assert lines[3] == "🌊 *BALTIC DRY INDEX*"
    assert lines[4] == "> *BDI*  `1850`  +25 (+1.37%) 🟢"
    assert lines[5] == ""
    assert lines[6] == "⚓ *ROTAS CAPESIZE*"
    assert lines[7] == "> *C3 Tubarao → Qingdao*  `$25.50/ton`  -0.30 (-1.16%) 🔴"
    assert lines[8] == "> *C5 W.Australia → Qingdao*  `$10.20/ton`  estável ▪️"
    assert lines[9] == "> *C5TC Timecharter Avg*  `15000/day`  +200 (+1.35%) 🟢"
    assert lines[10] == ""
    assert lines[11] == "🚢 *INDICES POR TIPO*"
    assert lines[12] == "> *Capesize (100k+ DWT)*  `2100`  -50 (-2.33%) 🔴"
    assert lines[13] == "> *Panamax (60-80k DWT)*  `1200`  estável ▪️"
    assert lines[14] == "> *Supramax (45-60k DWT)*  `800`  +10 (+1.27%) 🟢"
    assert lines[15] == "> *Handysize (15-35k DWT)*  `500`  -5 (-0.99%) 🔴"
    assert len(lines) == 16

    # C2/C7 routes absent from data → their lines are skipped entirely
    assert "C2 Tubarao" not in msg
    assert "C7 Bolivar" not in msg
