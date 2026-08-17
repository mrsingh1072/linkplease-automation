"""Tests for GET /stats."""
import pytest
from datetime import datetime, timezone


async def test_stats_initial_zeros(app_client):
    resp = await app_client.get("/stats")
    assert resp.status_code == 200
    data = resp.json()
    assert data == {"sent": 0, "failed": 0, "queued": 0, "duplicates_blocked": 0}


async def test_stats_shape(app_client):
    resp = await app_client.get("/stats")
    assert resp.status_code == 200
    data = resp.json()
    assert set(data.keys()) == {"sent", "failed", "queued", "duplicates_blocked"}


async def test_stats_queued_reflects_pending_delivery(app_client, test_db):
    now = datetime.now(timezone.utc)
    await test_db.deliveries.insert_one({
        "delivery_id": "d1",
        "user_id": "u1",
        "rule_id": "r1",
        "comment_id": "c1",
        "message": "msg",
        "status": "pending",
        "dm_id": None,
        "idempotency_key": "attempt:abc",
        "attempts": 0,
        "reconciliation_attempts": 0,
        "retry_after": None,
        "created_at": now,
        "updated_at": now,
    })
    resp = await app_client.get("/stats")
    assert resp.json()["queued"] == 1


async def test_stats_queued_includes_claimed_and_accepted(app_client, test_db):
    now = datetime.now(timezone.utc)
    base = {
        "user_id": "u1", "rule_id": "r1", "comment_id": "c1",
        "message": "msg", "dm_id": None, "idempotency_key": "k",
        "attempts": 0, "reconciliation_attempts": 0,
        "retry_after": None, "created_at": now, "updated_at": now,
    }
    await test_db.deliveries.insert_one({**base, "delivery_id": "d1", "status": "pending"})
    await test_db.deliveries.insert_one({**base, "delivery_id": "d2", "user_id": "u2", "status": "claimed"})
    await test_db.deliveries.insert_one({**base, "delivery_id": "d3", "user_id": "u3", "status": "accepted", "dm_id": "dm1"})
    resp = await app_client.get("/stats")
    assert resp.json()["queued"] == 3


async def test_stats_sent_reflects_delivered(app_client, test_db):
    now = datetime.now(timezone.utc)
    await test_db.deliveries.insert_one({
        "delivery_id": "d1", "user_id": "u1", "rule_id": "r1",
        "comment_id": "c1", "message": "msg", "status": "delivered",
        "dm_id": "dm_abc", "idempotency_key": "k", "attempts": 1,
        "reconciliation_attempts": 0, "retry_after": None,
        "created_at": now, "updated_at": now,
    })
    resp = await app_client.get("/stats")
    assert resp.json()["sent"] == 1
    assert resp.json()["failed"] == 0
    assert resp.json()["queued"] == 0


async def test_stats_failed_reflects_failed(app_client, test_db):
    now = datetime.now(timezone.utc)
    await test_db.deliveries.insert_one({
        "delivery_id": "d1", "user_id": "u1", "rule_id": "r1",
        "comment_id": "c1", "message": "msg", "status": "failed",
        "dm_id": None, "idempotency_key": "k", "attempts": 5,
        "reconciliation_attempts": 0, "retry_after": None,
        "created_at": now, "updated_at": now,
    })
    resp = await app_client.get("/stats")
    assert resp.json()["failed"] == 1


async def test_stats_duplicates_blocked_from_db(app_client, test_db):
    await test_db.app_stats.update_one(
        {"_id": "counters"},
        {"$set": {"duplicates_blocked": 7}},
        upsert=True,
    )
    resp = await app_client.get("/stats")
    assert resp.json()["duplicates_blocked"] == 7


async def test_stats_cancelled_not_counted(app_client, test_db):
    """Cancelled deliveries must not appear in any counter."""
    now = datetime.now(timezone.utc)
    await test_db.deliveries.insert_one({
        "delivery_id": "d1", "user_id": "u1", "rule_id": "r1",
        "comment_id": "c1", "message": "msg", "status": "cancelled",
        "dm_id": None, "idempotency_key": "k", "attempts": 0,
        "reconciliation_attempts": 0, "retry_after": None,
        "created_at": now, "updated_at": now,
    })
    resp = await app_client.get("/stats")
    data = resp.json()
    assert data["sent"] == 0
    assert data["failed"] == 0
    assert data["queued"] == 0
