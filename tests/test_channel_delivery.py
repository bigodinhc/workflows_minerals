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


def test_build_channel_payload_respeita_teto_com_texto_tag_denso():
    """Overhead de tag pode passar de 250% em texto denso (`a` repetido) —
    um RAW_TEXT_LIMIT fixo não segura isso. fit_raw_to_limit precisa."""
    from bot.channel_delivery import build_channel_payload, to_telegram_html, TELEGRAM_TEXT_LIMIT

    dense = "`a` " * 825  # 3300 chars crus == o antigo RAW_TEXT_LIMIT
    assert len(to_telegram_html(dense)) > TELEGRAM_TEXT_LIMIT  # prova a premissa do bug

    partes = build_channel_payload(dense)
    assert all(len(p) <= TELEGRAM_TEXT_LIMIT for p in partes)


def test_build_channel_payload_respeita_teto_com_mensagem_realista_densa():
    """Painel de cotação multi-linha (formato dos 3 crons) com overhead de
    tag alto o bastante pra estourar sem o fit — cada parte ainda cabe."""
    from bot.channel_delivery import build_channel_payload, to_telegram_html, TELEGRAM_TEXT_LIMIT

    raw = "\n".join(
        f"*Produto {i}*  `$10{i}.50/dmt`  +0.55 (+0.53%)" for i in range(150)
    )
    assert len(to_telegram_html(raw)) > TELEGRAM_TEXT_LIMIT  # prova a premissa do bug

    partes = build_channel_payload(raw)
    assert all(len(p) <= TELEGRAM_TEXT_LIMIT for p in partes)


def test_fit_raw_to_limit_nao_mexe_em_conteudo_que_ja_cabe():
    """Guarda contra o fit helper aparar quando não precisa — mensagem
    curta e normal (estilo marker_for flat/normal) sai idêntica."""
    from bot.channel_delivery import fit_raw_to_limit

    raw = "\n".join(f"*Produto {i}*  `$10{i}.50`  +0.55 (+0.53%) ↑" for i in range(60))
    assert fit_raw_to_limit(raw) == raw


def test_truncate_to_line_boundary_nao_corta_no_meio_da_linha():
    """O helper de truncamento cru (RAW_TEXT_LIMIT) nunca corta uma linha
    ao meio — corta pra trás até a última quebra de linha completa."""
    from bot.channel_delivery import _truncate_to_line_boundary

    linhas = [f"Linha {i} — CFR Qingdao $/DMT  `$107.9`" for i in range(200)]
    raw = "\n".join(linhas)
    limite = 3300

    # prova a premissa do bug: o corte ingênuo por índice pendura uma crase
    assert raw[:limite].count("`") % 2 == 1

    cortado = _truncate_to_line_boundary(raw, limite)
    assert len(cortado) <= limite
    # nenhuma linha cortada ao meio: o resultado é um prefixo exato de
    # linhas completas do original, sem crase (marcador) pendurada
    assert raw.startswith(cortado)
    assert cortado == "" or raw[len(cortado):len(cortado) + 1] == "\n"
    assert cortado.count("`") % 2 == 0


@pytest.mark.asyncio
async def test_post_report_to_channel_loga_truncamento(channel, mock_bot, caplog):
    """Truncamento no /store-draft precisa ficar visível no log (Railway) —
    antes era silencioso."""
    import logging
    from bot.channel_delivery import RAW_TEXT_LIMIT

    linhas = [f"Linha {i} — CFR Qingdao $/DMT  `$107.9`" for i in range(300)]
    raw = "\n".join(linhas)
    assert len(raw) > RAW_TEXT_LIMIT

    with caplog.at_level(logging.WARNING, logger="bot.channel_delivery"):
        result = await channel.post_report_to_channel(raw)

    assert result["ok"] is True
    assert any("truncat" in rec.message.lower() for rec in caplog.records)
    # cada mensagem enviada carrega só linhas inteiras — sem crase pendurada
    # nem em <pre> (bloco copiável) nem no post convertido
    for call in mock_bot.send_message.await_args_list:
        sent_text = call.args[1]
        assert sent_text.count("`") % 2 == 0


def test_build_channel_payload_guarda_bloco_gigante():
    """When copy_block alone exceeds 4096 due to HTML escape inflation,
    fall back to readable post only — the guard branch."""
    from bot.channel_delivery import build_channel_payload, COPY_LABEL
    # Raw: marker line + a separate line of & chars to make escaped copy
    # block exceed limit. escape_html turns & → &amp; (1→5), so 850 &'s =
    # 4250 escaped chars. copy_block overhead is ~40 chars → total ~4290 >
    # 4096 → guard triggers. Two lines (not one) so fit_raw_to_limit can
    # drop the noisy &-line by whole lines and keep the marker line intact.
    raw = "*marker*\n" + "&" * 850
    partes = build_channel_payload(raw)

    # Guard should return readable post only (not two messages with copy block)
    assert len(partes) == 1
    # Readable part has the converted marker
    assert "<b>marker</b>" in partes[0]
    # Copy block is NOT included (guard prevented it)
    assert COPY_LABEL not in partes[0]


@pytest.mark.asyncio
async def test_post_envia_duas_mensagens_quando_divide(channel, mock_bot):
    raw = "\n".join(f"*Produto {i}*  `$10{i}.50`  +0.55 (+0.53%) ↑" for i in range(60))
    result = await channel.post_report_to_channel(raw)
    assert result["ok"] is True
    assert mock_bot.send_message.await_count == 2
    # message_id é o da primeira mensagem (o post legível)
    assert result["message_id"] == 42


@pytest.mark.asyncio
async def test_post_envia_uma_mensagem_quando_cabe(channel, mock_bot):
    result = await channel.post_report_to_channel("📊 *MINERALS TRADING*\nBDI  `1850`  +25 ↑")
    assert result["ok"] is True
    assert mock_bot.send_message.await_count == 1
    assert channel.COPY_LABEL in mock_bot.send_message.await_args.args[1]


@pytest.mark.asyncio
async def test_post_falha_do_bloco_nao_derruba_o_post(channel, mock_bot):
    from unittest.mock import MagicMock
    mock_bot.send_message.side_effect = [
        MagicMock(message_id=42),
        RuntimeError("boom no bloco"),
    ]
    raw = "\n".join(f"*Produto {i}*  `$10{i}.50`  +0.55 (+0.53%) ↑" for i in range(60))
    result = await channel.post_report_to_channel(raw)
    assert result["ok"] is True            # o principal foi entregue
    assert result["message_id"] == 42
    assert "copy_block_failed" in result["error"]


@pytest.mark.asyncio
async def test_post_falha_da_primeira_mensagem_e_erro(channel, mock_bot):
    mock_bot.send_message.side_effect = RuntimeError("telegram fora do ar")
    result = await channel.post_report_to_channel("📊 *MINERALS TRADING*")
    assert result["ok"] is False
    assert result["message_id"] is None
    assert "telegram fora do ar" in result["error"]
