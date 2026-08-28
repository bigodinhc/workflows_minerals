"""O 400 do _MainChatSink levou um mês para ser diagnosticado porque o log
dizia apenas "400 Client Error: Bad Request for url: ..." — o raise_for_status
dispara antes de alguém ler o body, e é o body que traz o motivo real
("can't parse entities: ..."). Além disso a URL do erro carrega o token do bot.
"""
import pytest
import requests

from execution.integrations.telegram_client import TelegramClient, _redact_token

TOKEN = "8105492924:AAEUyuC4KKvpBaSCbNJ3vbqpZmlA-5QyVuA"


class _Resp:
    def __init__(self, payload, text="", status=400):
        self._payload, self.text, self.status_code = payload, text, status

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload

    def raise_for_status(self):
        raise requests.HTTPError(
            f"400 Client Error: Bad Request for url: https://api.telegram.org/bot{TOKEN}/sendMessage",
            response=self,
        )


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", TOKEN)
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "42")
    return TelegramClient()


def _send(client, monkeypatch, resp):
    monkeypatch.setattr(requests, "post", lambda *a, **k: resp)
    with pytest.raises(requests.HTTPError):
        client.send_message(text="oi")


class TestRedactToken:
    def test_strips_the_bot_token_from_a_url(self):
        out = _redact_token(f"for url: https://api.telegram.org/bot{TOKEN}/sendMessage")
        assert TOKEN not in out
        assert "/bot***/sendMessage" in out

    def test_leaves_unrelated_text_alone(self):
        assert _redact_token("nada a redigir aqui") == "nada a redigir aqui"


class TestErrorLogging:
    """WorkflowLogger escreve em stdout via print, nao pelo modulo logging —
    por isso capsys, e nao caplog. Com caplog estes testes passariam vazios."""

    def test_logs_the_telegram_description_not_just_the_status(self, client, monkeypatch, capsys):
        """O motivo real que faltava no log de producao."""
        resp = _Resp({"ok": False, "error_code": 400,
                      "description": "Bad Request: can't parse entities: Can't find end of the entity"})
        _send(client, monkeypatch, resp)
        out = capsys.readouterr().out
        assert "can't parse entities" in out

    def test_never_leaks_the_bot_token(self, client, monkeypatch, capsys):
        resp = _Resp({"ok": False, "description": "Bad Request: chat not found"})
        _send(client, monkeypatch, resp)
        out = capsys.readouterr().out
        assert "Failed to send message" in out, "guarda contra passar vazio"
        assert TOKEN not in out
        assert "/bot***/" in out

    def test_falls_back_to_raw_body_when_the_response_is_not_json(self, client, monkeypatch, capsys):
        resp = _Resp(None, text="<html>502 upstream</html>")
        _send(client, monkeypatch, resp)
        assert "502 upstream" in capsys.readouterr().out

    def test_still_reraises_so_callers_keep_their_behaviour(self, client, monkeypatch):
        """O EventBus depende da exceção para logar "sink ... failed"."""
        resp = _Resp({"ok": False, "description": "x"})
        _send(client, monkeypatch, resp)  # o pytest.raises interno já afirma isso
