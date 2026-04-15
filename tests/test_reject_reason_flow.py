"""Tests for the rejection-reason capture flow.

Uses fakeredis + direct calls to the helper functions in app.py. The
Flask request layer is tested in integration tests elsewhere; here we
verify the state machine in isolation.
"""
import time
import pytest
import fakeredis


@pytest.fixture
def fake_redis(monkeypatch):
    fake = fakeredis.FakeRedis(decode_responses=True)
    # app.py uses `import redis_queries` (bare name) which resolves to a
    # different sys.modules entry than `from webhook import redis_queries`.
    # Patch both so begin_reject_reason (in app.py) hits the fake client.
    from webhook import redis_queries as rq_pkg
    import redis_queries as rq_bare  # noqa: PLC0415
    for rq in (rq_pkg, rq_bare):
        monkeypatch.setattr(rq, "_get_client", lambda: fake)
        monkeypatch.setattr(rq, "_client", None)
    return fake


@pytest.fixture(autouse=True)
def _reset_state():
    from webhook import app as webhook_app
    webhook_app.REJECT_REASON_STATE.clear()
    webhook_app.ADJUST_STATE.clear()
    yield
    webhook_app.REJECT_REASON_STATE.clear()
    webhook_app.ADJUST_STATE.clear()


def test_begin_reject_reason_stores_state_and_saves_feedback(fake_redis):
    from webhook.app import begin_reject_reason, REJECT_REASON_STATE
    from webhook.redis_queries import list_feedback
    key = begin_reject_reason(
        chat_id=999, action="curate_reject",
        item_id="abc123", title="Sample title",
    )
    assert key is not None
    assert 999 in REJECT_REASON_STATE
    assert REJECT_REASON_STATE[999]["feedback_key"] == key
    entries = list_feedback(limit=10)
    assert len(entries) == 1
    assert entries[0]["reason"] == ""
    assert entries[0]["item_id"] == "abc123"
    assert entries[0]["title"] == "Sample title"


def test_consume_reject_reason_with_text(fake_redis):
    from webhook.app import begin_reject_reason, consume_reject_reason, REJECT_REASON_STATE
    from webhook.redis_queries import list_feedback
    begin_reject_reason(chat_id=999, action="curate_reject", item_id="x", title="T")
    consumed = consume_reject_reason(chat_id=999, text="não é iron ore")
    assert consumed == ("saved", "não é iron ore")
    assert 999 not in REJECT_REASON_STATE
    entries = list_feedback(limit=10)
    assert entries[0]["reason"] == "não é iron ore"


def test_consume_reject_reason_skip_pt(fake_redis):
    from webhook.app import begin_reject_reason, consume_reject_reason
    begin_reject_reason(chat_id=999, action="curate_reject", item_id="x", title="T")
    consumed = consume_reject_reason(chat_id=999, text="pular")
    assert consumed == ("skipped", "")


def test_consume_reject_reason_skip_en(fake_redis):
    from webhook.app import begin_reject_reason, consume_reject_reason
    begin_reject_reason(chat_id=999, action="curate_reject", item_id="x", title="T")
    consumed = consume_reject_reason(chat_id=999, text="SKIP")
    assert consumed == ("skipped", "")


def test_consume_reject_reason_no_state_returns_none(fake_redis):
    from webhook.app import consume_reject_reason
    consumed = consume_reject_reason(chat_id=999, text="random text")
    assert consumed is None


def test_consume_reject_reason_expired_state_returns_none(fake_redis):
    from webhook import app as webhook_app
    webhook_app.begin_reject_reason(chat_id=999, action="curate_reject", item_id="x", title="T")
    # Force expiration
    webhook_app.REJECT_REASON_STATE[999]["expires_at"] = time.time() - 10
    consumed = webhook_app.consume_reject_reason(chat_id=999, text="too late")
    assert consumed is None
    assert 999 not in webhook_app.REJECT_REASON_STATE


def test_adjust_state_takes_precedence_in_handle_message(fake_redis, monkeypatch):
    """End-to-end: with BOTH states set, the adjust handler consumes the
    message and the reject feedback reason remains empty.

    Drives the real Flask handler via test_client so the cascade order in
    telegram_webhook is exercised, not just asserted by inspection. Relies
    on the synchronous `del ADJUST_STATE[chat_id]` happening BEFORE the
    daemon thread starts — no thread join needed.
    """
    from webhook import app as webhook_app
    from webhook.redis_queries import list_feedback

    # Stub the heavy AI processor; we only care about the cascade decision
    monkeypatch.setattr(webhook_app, "process_adjustment_async",
                        lambda *args, **kwargs: None)

    webhook_app.ADJUST_STATE[999] = {"draft_id": "d1", "awaiting_feedback": True}
    webhook_app.begin_reject_reason(chat_id=999, action="curate_reject",
                                    item_id="x", title="T")

    client = webhook_app.app.test_client()
    payload = {"message": {"chat": {"id": 999},
                           "text": "this should go to ADJUST not REJECT"}}
    resp = client.post("/webhook", json=payload)
    assert resp.status_code == 200

    # ADJUST consumed the message (cleared its state synchronously in the dispatch)
    assert 999 not in webhook_app.ADJUST_STATE
    # REJECT state still present — was NOT consumed
    assert 999 in webhook_app.REJECT_REASON_STATE
    # The placeholder feedback's reason was NOT overwritten with the message text
    entries = list_feedback(limit=10)
    assert entries, "begin_reject_reason should have left a placeholder entry"
    assert entries[0]["reason"] == ""
