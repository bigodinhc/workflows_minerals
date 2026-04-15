"""Bot navigation command formatters.

Each handler returns a plain string (Markdown-safe) for the webhook
layer to send via Telegram. Callback-producing handlers also return an
optional reply_markup dict.

The handlers here do not know about Flask, requests, or Telegram — they
consume webhook.redis_queries and produce text. app.py wires them to
the chat.
"""
from webhook import redis_queries


_HELP_TEXT = """*COMANDOS*

/queue — items aguardando
/history — arquivo (últimos 10)
/rejections — recusas (últimas 10)
/stats — contadores de hoje
/status — saúde do sistema
/reprocess <id> — re-dispara pipeline
/add, /list — contatos
/cancel — abortar fluxo"""


def format_help() -> str:
    """Return the /help text (static)."""
    return _HELP_TEXT
