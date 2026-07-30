"""Tests for posting client reports to the private Telegram channel."""
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "webhook"))

import pytest
from aiogram.exceptions import TelegramRetryAfter


def _retry_after(seconds: int = 0) -> TelegramRetryAfter:
    return TelegramRetryAfter(method=MagicMock(), message="flood", retry_after=seconds)


@pytest.fixture
def mock_bot():
    bot = AsyncMock()
    bot.send_message = AsyncMock(return_value=MagicMock(message_id=42))
    bot.send_document = AsyncMock(return_value=MagicMock(message_id=43))
    bot.pin_chat_message = AsyncMock()
    return bot


@pytest.fixture
def channel(mock_bot):
    """Patch bot + channel id; yields the module under test."""
    import bot.channel_delivery as cd
    with patch.object(cd, "get_bot", return_value=mock_bot), \
         patch.object(cd, "TELEGRAM_CLIENT_CHANNEL_ID", "-1001234"):
        yield cd


def test_escape_html():
    from bot.channel_delivery import escape_html
    assert escape_html("a < b & c > d") == "a &lt; b &amp; c &gt; d"
    assert escape_html('say "hi"') == 'say "hi"'  # quotes stay readable


def test_to_telegram_html_converts_whatsapp_markers():
    from bot.channel_delivery import to_telegram_html
    assert to_telegram_html("*IRON ORE DAILY*") == "<b>IRON ORE DAILY</b>"
    assert to_telegram_html("_em alta_") == "<i>em alta</i>"
    assert to_telegram_html("`62% Fe`") == "<code>62% Fe</code>"
    assert to_telegram_html("```col1  col2\n1     2```") == "<pre>col1  col2\n1     2</pre>"


def test_to_telegram_html_escapes_before_converting():
    from bot.channel_delivery import to_telegram_html
    assert to_telegram_html("*a < b & c*") == "<b>a &lt; b &amp; c</b>"


def test_to_telegram_html_unbalanced_markers_stay_literal():
    from bot.channel_delivery import to_telegram_html
    # a stray asterisk must never break the post — it renders as-is
    assert to_telegram_html("preço * volume") == "preço * volume"
    assert to_telegram_html("nota_de_rodapé") == "nota_de_rodapé"  # intra-word _ untouched
    assert to_telegram_html("*sem fechamento") == "*sem fechamento"


def test_to_telegram_html_mixed_message():
    from bot.channel_delivery import to_telegram_html
    src = "📊 *MINERALS TRADING*\n*Iron Ore Daily*\n`ATIVO · 09/JUL`\nPlatts: _firme_"
    out = to_telegram_html(src)
    assert "<b>MINERALS TRADING</b>" in out
    assert "<b>Iron Ore Daily</b>" in out
    assert "<code>ATIVO · 09/JUL</code>" in out
    assert "<i>firme</i>" in out


def test_quote_lines_become_blockquote():
    from bot.channel_delivery import to_telegram_html
    src = "lead\n\n> *Ago/26*  `$103.50`  +0.55 🟢\n> *Set/26*  `$103.05`  +0.40 🟢\n\nfim"
    out = to_telegram_html(src)
    assert (
        "<blockquote><b>Ago/26</b>  <code>$103.50</code>  +0.55 🟢\n"
        "<b>Set/26</b>  <code>$103.05</code>  +0.40 🟢</blockquote>"
    ) in out
    assert out.startswith("lead")
    assert out.rstrip().endswith("fim")


def test_single_quote_line_becomes_blockquote():
    from bot.channel_delivery import to_telegram_html
    out = to_telegram_html('> _"citação do CEO"_ — Fonte')
    assert out.startswith("<blockquote>")
    assert "<i>" in out and out.rstrip().endswith("</blockquote>")


def test_text_without_quotes_unchanged_by_quote_pass():
    from bot.channel_delivery import to_telegram_html
    out = to_telegram_html("*bold* e `code`, sem citação. a > b")
    assert "<blockquote" not in out
    assert "a &gt; b" in out  # '>' no meio da linha não é citação


@pytest.mark.asyncio
async def test_post_text_only(channel, mock_bot):
    result = await channel.post_report_to_channel("*Preços* <em alta> & subindo")
    assert result == {"ok": True, "message_id": 42, "error": None}
    args, kwargs = mock_bot.send_message.await_args
    assert args[0] == "-1001234"
    assert "<b>Preços</b> &lt;em alta&gt; &amp;" in args[1]
    assert kwargs["parse_mode"] == "HTML"
    assert kwargs["disable_notification"] is False
    mock_bot.send_document.assert_not_awaited()
    mock_bot.pin_chat_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_post_with_pdf(channel, mock_bot):
    result = await channel.post_report_to_channel(
        "Relatório", pdf=b"%PDF-1.4 x", pdf_filename="Minerals_Report.pdf",
    )
    assert result["ok"] is True
    mock_bot.send_document.assert_awaited_once()
    _, kwargs = mock_bot.send_document.await_args
    assert kwargs["caption"] == "Minerals_Report.pdf"
    assert kwargs["parse_mode"] == "HTML"


@pytest.mark.asyncio
async def test_pdf_failure_does_not_block_text(channel, mock_bot):
    mock_bot.send_document.side_effect = RuntimeError("boom")
    result = await channel.post_report_to_channel("Relatório", pdf=b"%PDF")
    assert result["ok"] is True
    assert result["message_id"] == 42
    assert "pdf_send_failed" in result["error"]


@pytest.mark.asyncio
async def test_silent_and_pin(channel, mock_bot):
    result = await channel.post_report_to_channel("Msg", silent=True, pin=True)
    assert result["ok"] is True
    _, kwargs = mock_bot.send_message.await_args
    assert kwargs["disable_notification"] is True
    mock_bot.pin_chat_message.assert_awaited_once()


@pytest.mark.asyncio
async def test_flood_wait_retries_then_succeeds(channel, mock_bot):
    mock_bot.send_message.side_effect = [
        _retry_after(0), MagicMock(message_id=99),
    ]
    result = await channel.post_report_to_channel("Msg")
    assert result["ok"] is True
    assert result["message_id"] == 99
    assert mock_bot.send_message.await_count == 2


@pytest.mark.asyncio
async def test_hard_failure_returns_error_dict(channel, mock_bot):
    mock_bot.send_message.side_effect = RuntimeError("bot is not a member")
    result = await channel.post_report_to_channel("Msg")
    assert result["ok"] is False
    assert result["message_id"] is None
    assert "bot is not a member" in result["error"]


@pytest.mark.asyncio
async def test_get_bot_failure_returns_error_dict():
    import bot.channel_delivery as cd
    with patch.object(cd, "get_bot", side_effect=RuntimeError("token")), \
         patch.object(cd, "TELEGRAM_CLIENT_CHANNEL_ID", "-1001234"):
        result = await cd.post_report_to_channel("Msg")
    assert result["ok"] is False
    assert result["message_id"] is None
    assert "token" in result["error"]


@pytest.mark.asyncio
async def test_missing_channel_id_fails_cleanly(mock_bot):
    import bot.channel_delivery as cd
    with patch.object(cd, "get_bot", return_value=mock_bot), \
         patch.object(cd, "TELEGRAM_CLIENT_CHANNEL_ID", ""):
        result = await cd.post_report_to_channel("Msg")
    assert result["ok"] is False
    assert "TELEGRAM_CLIENT_CHANNEL_ID" in result["error"]
    mock_bot.send_message.assert_not_awaited()


# ── bloco copiável pro WhatsApp ──

def test_has_whatsapp_markers_detecta_cada_marcador():
    from bot.channel_delivery import has_whatsapp_markers
    assert has_whatsapp_markers("*IRON ORE*")
    assert has_whatsapp_markers("preço `$107.90` hoje")
    assert has_whatsapp_markers("_em alta_")
    assert has_whatsapp_markers("> citação do CEO")
    assert has_whatsapp_markers("linha 1\n> citação")


def test_has_whatsapp_markers_falso_sem_marcador():
    from bot.channel_delivery import has_whatsapp_markers
    assert not has_whatsapp_markers("📄 relatorio-platts-30-07.pdf")
    assert not has_whatsapp_markers("")
    assert not has_whatsapp_markers("texto puro sem formatação")


def test_build_copy_block_preserva_o_texto_cru():
    from bot.channel_delivery import build_copy_block, COPY_LABEL
    raw = "📊 *MINERALS TRADING*\nBDI  `1850`  +25 ↑"
    bloco = build_copy_block(raw)
    assert bloco.startswith(COPY_LABEL)
    assert "<pre>" in bloco and "</pre>" in bloco
    # os marcadores sobrevivem literalmente — é o ponto do bloco
    assert "*MINERALS TRADING*" in bloco
    assert "`1850`" in bloco


def test_build_copy_block_escapa_html():
    from bot.channel_delivery import build_copy_block
    bloco = build_copy_block("lucro > custo & margem < 10%")
    assert "&gt;" in bloco and "&amp;" in bloco and "&lt;" in bloco


def test_build_channel_payload_uma_mensagem_quando_cabe():
    from bot.channel_delivery import build_channel_payload, COPY_LABEL
    partes = build_channel_payload("📊 *MINERALS TRADING*\nBDI  `1850`  +25 ↑")
    assert len(partes) == 1
    assert "<b>MINERALS TRADING</b>" in partes[0]   # parte bonita
    assert COPY_LABEL in partes[0]                   # bloco junto


def test_build_channel_payload_divide_quando_estoura():
    from bot.channel_delivery import build_channel_payload, COPY_LABEL, TELEGRAM_TEXT_LIMIT
    # 60 linhas de cotação com marcador → pretty + bloco passam de 4096
    raw = "\n".join(f"*Produto {i}*  `$10{i}.50`  +0.55 (+0.53%) ↑" for i in range(60))
    partes = build_channel_payload(raw)
    assert len(partes) == 2
    assert COPY_LABEL not in partes[0]     # 1ª é só o post legível
    assert partes[1].startswith(COPY_LABEL)
    assert all(len(p) <= TELEGRAM_TEXT_LIMIT for p in partes)


def test_build_channel_payload_pula_bloco_sem_marcadores():
    from bot.channel_delivery import build_channel_payload, COPY_LABEL
    partes = build_channel_payload("📄 relatorio-platts-30-07.pdf")
    assert len(partes) == 1
    assert COPY_LABEL not in partes[0]


def test_build_channel_payload_nunca_trunca_conteudo():
    from bot.channel_delivery import build_channel_payload
    raw = "\n".join(f"*Produto {i}*  `$10{i}.50`  +0.55 (+0.53%) ↑" for i in range(60))
    partes = build_channel_payload(raw)
    # toda linha do original aparece em alguma das partes
    for i in range(60):
        assert f"Produto {i}" in "".join(partes)


def test_raw_text_limit_cabe_no_teto_com_overhead_de_tags():
    from bot.channel_delivery import RAW_TEXT_LIMIT, TELEGRAM_TEXT_LIMIT
    # overhead medido nas mensagens reais: ~21%
    assert RAW_TEXT_LIMIT * 1.21 <= TELEGRAM_TEXT_LIMIT
