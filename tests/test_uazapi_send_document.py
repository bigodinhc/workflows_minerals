"""Unit tests for UazapiClient.send_document (POST /send/media)."""
from __future__ import annotations

import os
import pytest
from unittest.mock import patch, MagicMock

from execution.integrations.uazapi_client import UazapiClient


@pytest.fixture(autouse=True)
def _env():
    os.environ["UAZAPI_URL"] = "https://test.uazapi.example.com"
    os.environ["UAZAPI_TOKEN"] = "fake-token"
    yield
    os.environ.pop("UAZAPI_URL", None)
    os.environ.pop("UAZAPI_TOKEN", None)


def _mock_post_ok():
    m = MagicMock()
    m.status_code = 200
    m.json.return_value = {"messageId": "abc", "status": "sent"}
    return m


def test_send_document_posts_expected_payload():
    client = UazapiClient()
    with patch("execution.integrations.uazapi_client.requests.post",
               return_value=_mock_post_ok()) as post:
        client.send_document(
            number="5511987654321",
            file_url="https://graph-cdn.example.com/download?sig=xyz",
            doc_name="Minerals_Report_042226.pdf",
        )
    post.assert_called_once()
    args, kwargs = post.call_args
    assert args[0] == "https://test.uazapi.example.com/send/media"
    assert kwargs["headers"] == {"token": "fake-token", "Content-Type": "application/json"}
    body = kwargs["json"]
    assert body["number"] == "5511987654321"
    assert body["type"] == "document"
    assert body["file"] == "https://graph-cdn.example.com/download?sig=xyz"
    assert body["docName"] == "Minerals_Report_042226.pdf"
    assert body.get("text", "") == ""


def test_send_document_includes_caption_when_provided():
    client = UazapiClient()
    with patch("execution.integrations.uazapi_client.requests.post",
               return_value=_mock_post_ok()) as post:
        client.send_document(
            number="5511987654321",
            file_url="https://example.com/x.pdf",
            doc_name="x.pdf",
            caption="Novo relatório diário",
        )
    body = post.call_args.kwargs["json"]
    assert body["text"] == "Novo relatório diário"


def test_send_document_returns_json_response():
    client = UazapiClient()
    with patch("execution.integrations.uazapi_client.requests.post",
               return_value=_mock_post_ok()):
        result = client.send_document(
            number="5511987654321",
            file_url="https://example.com/x.pdf",
            doc_name="x.pdf",
        )
    assert result == {"messageId": "abc", "status": "sent"}


def test_send_document_raises_on_4xx():
    client = UazapiClient()
    bad = MagicMock()
    bad.status_code = 400
    bad.text = '{"error":"invalid number"}'
    bad.raise_for_status.side_effect = Exception("400 Bad Request")
    with patch("execution.integrations.uazapi_client.requests.post", return_value=bad):
        with pytest.raises(Exception):
            client.send_document(
                number="bad", file_url="x", doc_name="x.pdf",
            )
