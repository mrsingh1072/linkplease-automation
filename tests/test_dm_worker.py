"""
Tests for the DM worker: process_delivery(), acquire_rate_limit_slot(), run_dm_worker().

Uses real test MongoDB and mocked httpx clients.
"""
import asyncio
import uuid
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.workers.dm_worker import (
    MAX_SEND_ATTEMPTS,
    RATE_LIMIT_MAX,
    acquire_rate_limit_slot,
    process_delivery,
)


def _make_delivery(
    user_id="u1",
    rule_id="r1",
    comment_id="c1",
    attempts=0,
    status="claimed",
    retry_after=None,
) -> dict:
    now = datetime.now(timezone.utc)
    return {
        "delivery_id": str(uuid.uuid4()),
        "user_id": user_id,
        "rule_id": rule_id,
        "comment_id": comment_id,
        "message": "Test message",
        "status": status,
        "dm_id": None,
        "idempotency_key": f"attempt:{uuid.uuid4()}",
        "attempts": attempts,
        "reconciliation_attempts": 0,
        "retry_after": retry_after,
        "created_at": now,
        "updated_at": now,
    }


def _mock_202(dm_id="dm_test_123"):
    resp = MagicMock()
    resp.status_code = 202
    resp.json.return_value = {"dm_id": dm_id, "status": "queued"}
    return resp


def _mock_500():
    resp = MagicMock()
    resp.status_code = 500
    resp.text = "Internal Server Error"
    return resp


def _mock_400():
    resp = MagicMock()
    resp.status_code = 400
    resp.text = "Bad Request"
    return resp


def _mock_429(retry_after=30):
    resp = MagicMock()
    resp.status_code = 429
    resp.headers = {"Retry-After": str(retry_after)}
    return resp


# -----------------------------------------------------------------------
# process_delivery — success path
# -----------------------------------------------------------------------

async def test_process_delivery_202_sets_accepted(test_db):
    delivery = _make_delivery()
    await test_db.deliveries.insert_one(delivery)

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=_mock_202("dm_abc"))

    with patch("app.workers.dm_worker.acquire_rate_limit_slot", return_value=None):
        await process_delivery(delivery, test_db, mock_client)

    updated = await test_db.deliveries.find_one({"delivery_id": delivery["delivery_id"]})
    assert updated["status"] == "accepted"
    assert updated["dm_id"] == "dm_abc"
    assert updated["attempts"] == 1


async def test_process_delivery_202_idempotency_key_sent(test_db):
    """The idempotency key stored in the delivery must be forwarded to PseudoGram."""
    delivery = _make_delivery()
    await test_db.deliveries.insert_one(delivery)

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=_mock_202())

    with patch("app.workers.dm_worker.acquire_rate_limit_slot", return_value=None):
        await process_delivery(delivery, test_db, mock_client)

    call_kwargs = mock_client.post.call_args
    sent_headers = call_kwargs.kwargs.get("headers", {})
    assert sent_headers.get("Idempotency-Key") == delivery["idempotency_key"]


# -----------------------------------------------------------------------
# process_delivery — 500 retry
# -----------------------------------------------------------------------

async def test_process_delivery_500_sets_pending_with_backoff(test_db):
    delivery = _make_delivery(attempts=0)
    await test_db.deliveries.insert_one(delivery)

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=_mock_500())

    with patch("app.workers.dm_worker.acquire_rate_limit_slot", return_value=None):
        await process_delivery(delivery, test_db, mock_client)

    updated = await test_db.deliveries.find_one({"delivery_id": delivery["delivery_id"]})
    assert updated["status"] == "pending"
    assert updated["attempts"] == 1
    retry_after = updated["retry_after"]
    if retry_after.tzinfo is None:
        retry_after = retry_after.replace(tzinfo=timezone.utc)
    assert retry_after > datetime.now(timezone.utc)


async def test_process_delivery_500_same_idempotency_key_retained(test_db):
    """On transient failure, the idempotency_key must NOT change."""
    delivery = _make_delivery(attempts=1)
    original_key = delivery["idempotency_key"]
    await test_db.deliveries.insert_one(delivery)

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=_mock_500())

    with patch("app.workers.dm_worker.acquire_rate_limit_slot", return_value=None):
        await process_delivery(delivery, test_db, mock_client)

    updated = await test_db.deliveries.find_one({"delivery_id": delivery["delivery_id"]})
    assert updated["idempotency_key"] == original_key


async def test_process_delivery_500_max_attempts_marks_failed(test_db):
    delivery = _make_delivery(attempts=MAX_SEND_ATTEMPTS - 1)
    await test_db.deliveries.insert_one(delivery)

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=_mock_500())

    with patch("app.workers.dm_worker.acquire_rate_limit_slot", return_value=None):
        await process_delivery(delivery, test_db, mock_client)

    updated = await test_db.deliveries.find_one({"delivery_id": delivery["delivery_id"]})
    assert updated["status"] == "failed"
    assert updated["attempts"] == MAX_SEND_ATTEMPTS


# -----------------------------------------------------------------------
# process_delivery — 400 permanent failure
# -----------------------------------------------------------------------

async def test_process_delivery_400_marks_failed_immediately(test_db):
    delivery = _make_delivery(attempts=0)
    await test_db.deliveries.insert_one(delivery)

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=_mock_400())

    with patch("app.workers.dm_worker.acquire_rate_limit_slot", return_value=None):
        await process_delivery(delivery, test_db, mock_client)

    updated = await test_db.deliveries.find_one({"delivery_id": delivery["delivery_id"]})
    assert updated["status"] == "failed"
    # Should not retry — attempts only 1
    assert updated["attempts"] == 1


# -----------------------------------------------------------------------
# process_delivery — 429 rate limited
# -----------------------------------------------------------------------

async def test_process_delivery_429_sets_pending_with_retry_after(test_db):
    delivery = _make_delivery(attempts=0)
    await test_db.deliveries.insert_one(delivery)

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=_mock_429(retry_after=45))

    with patch("app.workers.dm_worker.acquire_rate_limit_slot", return_value=None):
        await process_delivery(delivery, test_db, mock_client)

    updated = await test_db.deliveries.find_one({"delivery_id": delivery["delivery_id"]})
    assert updated["status"] == "pending"
    # retry_after should be approximately 45 seconds from now
    retry_after = updated["retry_after"]
    if retry_after.tzinfo is None:
        retry_after = retry_after.replace(tzinfo=timezone.utc)
    diff = (retry_after - datetime.now(timezone.utc)).total_seconds()
    assert 40 <= diff <= 50


async def test_process_delivery_rate_limited_by_tracker_no_http_call(test_db):
    """If acquire_rate_limit_slot returns wait_time, no HTTP call must be made."""
    delivery = _make_delivery()
    await test_db.deliveries.insert_one(delivery)

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=_mock_202())

    # Simulate rate limiter returning a wait time
    with patch("app.workers.dm_worker.acquire_rate_limit_slot", return_value=30.0):
        await process_delivery(delivery, test_db, mock_client)

    mock_client.post.assert_not_called()
    updated = await test_db.deliveries.find_one({"delivery_id": delivery["delivery_id"]})
    assert updated["status"] == "pending"


# -----------------------------------------------------------------------
# Rate limit slot acquisition
# -----------------------------------------------------------------------

async def test_rate_limit_slot_allows_up_to_max(test_db):
    """First RATE_LIMIT_MAX calls should all succeed."""
    for i in range(RATE_LIMIT_MAX):
        result = await acquire_rate_limit_slot(test_db)
        assert result is None, f"Slot {i+1} should have been available"


async def test_rate_limit_slot_blocks_after_max(test_db):
    """After RATE_LIMIT_MAX slots, the next call must return a wait time."""
    for _ in range(RATE_LIMIT_MAX):
        await acquire_rate_limit_slot(test_db)

    wait = await acquire_rate_limit_slot(test_db)
    assert wait is not None
    assert wait > 0


async def test_rate_limit_is_concurrent_safe(test_db):
    """
    Concurrent calls must not grant more than RATE_LIMIT_MAX slots total.
    """
    results = await asyncio.gather(
        *[acquire_rate_limit_slot(test_db) for _ in range(RATE_LIMIT_MAX + 5)]
    )
    granted = [r for r in results if r is None]
    blocked = [r for r in results if r is not None]
    assert len(granted) == RATE_LIMIT_MAX
    assert len(blocked) == 5


# -----------------------------------------------------------------------
# Worker loop — atomic claiming
# -----------------------------------------------------------------------

async def test_worker_loop_claims_and_processes(test_db):
    """
    Verify the worker atomically transitions a delivery from pending to claimed
    and then to accepted.
    """
    import app.database as db_mod
    db_mod._client = None  # let worker use test settings

    delivery = _make_delivery(status="pending")
    delivery.pop("_id", None)
    await test_db.deliveries.insert_one(delivery)

    mock_resp = _mock_202("dm_worker_test")
    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=mock_resp)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("app.workers.dm_worker.acquire_rate_limit_slot", return_value=None), \
         patch("httpx.AsyncClient", return_value=mock_client):
        from app.workers.dm_worker import run_dm_worker

        worker_task = asyncio.create_task(run_dm_worker())
        # Give the worker time to process one delivery
        await asyncio.sleep(0.3)
        worker_task.cancel()
        try:
            await worker_task
        except asyncio.CancelledError:
            pass

    updated = await test_db.deliveries.find_one({"delivery_id": delivery["delivery_id"]})
    assert updated["status"] == "accepted"
