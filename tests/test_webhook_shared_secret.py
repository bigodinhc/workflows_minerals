"""Gate de shared-secret nos endpoints HTTP do webhook."""
import sys
from pathlib import Path
from typing import Optional
from unittest.mock import patch

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "webhook"))

import pytest

SECRET = "s3gr3d0-de-teste"


class _FakeRequest:
    def __init__(self, payload: Optional[dict] = None, headers: Optional[dict] = None):
        self._payload = payload or {}
        self.headers = headers or {}

    async def json(self):
        return self._payload


@pytest.fixture
def secret_env(monkeypatch):
    monkeypatch.setenv("WEBHOOK_SHARED_SECRET", SECRET)


def test_helper_fail_closed_sem_env(monkeypatch):
    monkeypatch.delenv("WEBHOOK_SHARED_SECRET", raising=False)
    from routes.api import require_shared_secret
    resp = require_shared_secret(_FakeRequest(headers={"X-Webhook-Secret": "qualquer"}))
    assert resp is not None
    assert resp.status == 500


@pytest.mark.parametrize("valor", ["", "   "])
def test_helper_fail_closed_env_vazio(monkeypatch, valor):
    monkeypatch.setenv("WEBHOOK_SHARED_SECRET", valor)
    from routes.api import require_shared_secret
    resp = require_shared_secret(_FakeRequest(headers={"X-Webhook-Secret": "qualquer"}))
    assert resp is not None
    assert resp.status == 500


def test_helper_rejeita_header_ausente(secret_env):
    from routes.api import require_shared_secret
    resp = require_shared_secret(_FakeRequest())
    assert resp is not None
    assert resp.status == 401


def test_helper_rejeita_header_errado(secret_env):
    from routes.api import require_shared_secret
    resp = require_shared_secret(_FakeRequest(headers={"X-Webhook-Secret": "errado"}))
    assert resp is not None
    assert resp.status == 401


def test_helper_aceita_header_correto(secret_env):
    from routes.api import require_shared_secret
    resp = require_shared_secret(_FakeRequest(headers={"X-Webhook-Secret": SECRET}))
    assert resp is None


@pytest.mark.asyncio
async def test_store_draft_sem_header_401_e_nada_persistido(secret_env):
    from routes.api import store_draft
    with patch("routes.api.drafts_set") as drafts:
        resp = await store_draft(
            _FakeRequest({"draft_id": "d1", "message": "conteúdo"})
        )
    assert resp.status == 401
    drafts.assert_not_called()


@pytest.mark.asyncio
async def test_test_ai_sem_header_401(secret_env):
    from routes.api import test_ai
    resp = await test_ai(_FakeRequest())
    assert resp.status == 401
