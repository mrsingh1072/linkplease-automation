"""Tests for POST /webhook."""
import json
import uuid

import pytest

from tests.conftest import TEST_API_KEY, make_signature, signed_webhook


def _comment_payload(
    event_id=None,
    comment_id=None,
    user_id=None,
    text="I want PRICE info",
    event_type="comment.created",
) -> dict:
    return {
        "event_id": event_id or str(uuid.uuid4()),
        "event_type": event_type,
        "comment_id": comment_id or str(uuid.uuid4()),
        "text": text,
        "from": {"user_id": user_id or str(uuid.uuid4())},
    }


async def _create_rule(app_client, keyword="PRICE", message="Price info!") -> str:
    resp = await app_client.post("/rules", json={"keyword": keyword, "dm_message": message})
    assert resp.status_code == 201
    return resp.json()["rule_id"]


# -----------------------------------------------------------------------
# Signature checks
# -----------------------------------------------------------------------

async def test_webhook_missing_signature_returns_401(app_client):
    payload = _comment_payload()
    raw = json.dumps(payload).encode()
    resp = await app_client.post(
        "/webhook",
        content=raw,
        headers={"Content-Type": "application/json"},
    )
    assert resp.status_code == 401


async def test_webhook_invalid_signature_returns_401(app_client):
    payload = _comment_payload()
    raw = json.dumps(payload).encode()
    resp = await app_client.post(
        "/webhook",
        content=raw,
        headers={
            "Content-Type": "application/json",
            "X-PseudoGram-Signature": "sha256=deadbeef",
        },
    )
    assert resp.status_code == 401


async def test_webhook_wrong_key_returns_401(app_client):
    payload = _comment_payload()
    raw, headers = signed_webhook(payload, key="wrong_key")
    resp = await app_client.post("/webhook", content=raw, headers=headers)
    assert resp.status_code == 401


async def test_webhook_valid_signature_returns_200(app_client):
    payload = _comment_payload()
    raw, headers = signed_webhook(payload)
    resp = await app_client.post("/webhook", content=raw, headers=headers)
    assert resp.status_code == 200


# -----------------------------------------------------------------------
# Event deduplication
# -----------------------------------------------------------------------

async def test_webhook_duplicate_event_id_is_idempotent(app_client, test_db):
    await _create_rule(app_client, "PRICE")
    payload = _comment_payload(text="PRICE info please")
    raw, headers = signed_webhook(payload)

    r1 = await app_client.post("/webhook", content=raw, headers=headers)
    r2 = await app_client.post("/webhook", content=raw, headers=headers)
    assert r1.status_code == 200
    assert r2.status_code == 200

    # Only one event record persisted
    count = await test_db.events.count_documents({"event_id": payload["event_id"]})
    assert count == 1

    # Only one delivery created — NOT two
    count = await test_db.deliveries.count_documents({})
    assert count == 1


async def test_duplicate_event_does_not_increment_duplicates_blocked(app_client, test_db):
    """
    Event dedup (duplicate event_id) must NOT increment duplicates_blocked.
    That counter is only for DM-level duplicates (same user+rule).
    """
    await _create_rule(app_client, "PRICE")
    payload = _comment_payload(text="PRICE now")
    raw, headers = signed_webhook(payload)

    await app_client.post("/webhook", content=raw, headers=headers)
    await app_client.post("/webhook", content=raw, headers=headers)

    stats_doc = await test_db.app_stats.find_one({"_id": "counters"})
    blocked = stats_doc.get("duplicates_blocked", 0) if stats_doc else 0
    assert blocked == 0


# -----------------------------------------------------------------------
# DM delivery creation
# -----------------------------------------------------------------------

async def test_matching_comment_creates_delivery(app_client, test_db):
    rule_id = await _create_rule(app_client, "PRICE")
    payload = _comment_payload(text="What is the PRICE?")
    raw, headers = signed_webhook(payload)
    resp = await app_client.post("/webhook", content=raw, headers=headers)
    assert resp.status_code == 200

    delivery = await test_db.deliveries.find_one({"rule_id": rule_id})
    assert delivery is not None
    assert delivery["status"] == "pending"
    assert delivery["user_id"] == payload["from"]["user_id"]
    assert delivery["comment_id"] == payload["comment_id"]


async def test_non_matching_comment_creates_no_delivery(app_client, test_db):
    await _create_rule(app_client, "PRICE")
    payload = _comment_payload(text="Hello world, no keyword here")
    raw, headers = signed_webhook(payload)
    await app_client.post("/webhook", content=raw, headers=headers)

    count = await test_db.deliveries.count_documents({})
    assert count == 0


# -----------------------------------------------------------------------
# DM duplicate protection (user + rule)
# -----------------------------------------------------------------------

async def test_same_user_same_rule_second_comment_blocked(app_client, test_db):
    """
    Same user comments twice with keyword. Second match must be blocked —
    NOT sent again. duplicates_blocked must be incremented exactly once.
    """
    rule_id = await _create_rule(app_client, "PRICE")
    user_id = str(uuid.uuid4())

    # First comment
    p1 = _comment_payload(user_id=user_id, text="What is the PRICE?")
    raw1, headers1 = signed_webhook(p1)
    await app_client.post("/webhook", content=raw1, headers=headers1)

    # Second comment (different event_id, same user, same keyword)
    p2 = _comment_payload(user_id=user_id, text="Tell me the PRICE again")
    raw2, headers2 = signed_webhook(p2)
    await app_client.post("/webhook", content=raw2, headers=headers2)

    # Still only one delivery
    count = await test_db.deliveries.count_documents({"user_id": user_id})
    assert count == 1

    stats_resp = await app_client.get("/stats")
    assert stats_resp.json()["duplicates_blocked"] == 1


async def test_different_users_same_rule_both_get_delivery(app_client, test_db):
    await _create_rule(app_client, "PRICE")
    user_a = str(uuid.uuid4())
    user_b = str(uuid.uuid4())

    p_a = _comment_payload(user_id=user_a, text="PRICE please")
    p_b = _comment_payload(user_id=user_b, text="PRICE info")

    for p in [p_a, p_b]:
        raw, headers = signed_webhook(p)
        await app_client.post("/webhook", content=raw, headers=headers)

    count = await test_db.deliveries.count_documents({})
    assert count == 2


async def test_same_user_different_rules_both_get_delivery(app_client, test_db):
    await _create_rule(app_client, "PRICE")
    await _create_rule(app_client, "PROMO")
    user_id = str(uuid.uuid4())

    payload = _comment_payload(user_id=user_id, text="I want PRICE and PROMO")
    raw, headers = signed_webhook(payload)
    await app_client.post("/webhook", content=raw, headers=headers)

    count = await test_db.deliveries.count_documents({"user_id": user_id})
    assert count == 2


# -----------------------------------------------------------------------
# comment.deleted
# -----------------------------------------------------------------------

async def test_comment_deleted_cancels_pending_delivery(app_client, test_db):
    await _create_rule(app_client, "PRICE")
    comment_id = str(uuid.uuid4())
    user_id = str(uuid.uuid4())

    # Create the delivery via comment.created
    p_create = _comment_payload(comment_id=comment_id, user_id=user_id, text="PRICE?")
    raw, headers = signed_webhook(p_create)
    await app_client.post("/webhook", content=raw, headers=headers)

    delivery = await test_db.deliveries.find_one({"comment_id": comment_id})
    assert delivery["status"] == "pending"

    # Now delete the comment
    p_delete = {
        "event_id": str(uuid.uuid4()),
        "event_type": "comment.deleted",
        "comment_id": comment_id,
        "from": {"user_id": user_id},
    }
    raw_d, headers_d = signed_webhook(p_delete)
    resp = await app_client.post("/webhook", content=raw_d, headers=headers_d)
    assert resp.status_code == 200

    delivery = await test_db.deliveries.find_one({"comment_id": comment_id})
    assert delivery["status"] == "cancelled"


# -----------------------------------------------------------------------
# Concurrent duplicate protection
# -----------------------------------------------------------------------

async def test_concurrent_same_event_deduplication(app_client, test_db):
    """Send the same event concurrently — only one delivery must be created."""
    import asyncio
    await _create_rule(app_client, "PRICE")
    payload = _comment_payload(text="PRICE!")
    raw, headers = signed_webhook(payload)

    results = await asyncio.gather(
        *[app_client.post("/webhook", content=raw, headers=headers) for _ in range(10)]
    )
    assert all(r.status_code == 200 for r in results)

    count = await test_db.deliveries.count_documents({})
    assert count == 1


async def test_missing_event_id_returns_400(app_client):
    payload = {"event_type": "comment.created", "comment_id": "c1", "text": "hi", "from": {"user_id": "u1"}}
    raw, headers = signed_webhook(payload)
    resp = await app_client.post("/webhook", content=raw, headers=headers)
    assert resp.status_code == 400


async def test_missing_event_type_returns_400(app_client):
    payload = {"event_id": "e1", "comment_id": "c1", "text": "hi", "from": {"user_id": "u1"}}
    raw, headers = signed_webhook(payload)
    resp = await app_client.post("/webhook", content=raw, headers=headers)
    assert resp.status_code == 400
