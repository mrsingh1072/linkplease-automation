"""Tests for POST /rules."""
import pytest


async def test_create_rule_returns_201(app_client):
    resp = await app_client.post("/rules", json={"keyword": "PRICE", "dm_message": "Here's the price list!"})
    assert resp.status_code == 201
    data = resp.json()
    assert data["keyword"] == "PRICE"
    assert data["dm_message"] == "Here's the price list!"
    assert "rule_id" in data
    assert data["rule_id"]  # non-empty


async def test_create_rule_response_shape(app_client):
    resp = await app_client.post("/rules", json={"keyword": "SALE", "dm_message": "Big sale!"})
    assert resp.status_code == 201
    data = resp.json()
    # Exactly these three keys
    assert set(data.keys()) == {"rule_id", "keyword", "dm_message"}


async def test_create_rule_persists_to_database(app_client, test_db):
    resp = await app_client.post("/rules", json={"keyword": "HELLO", "dm_message": "Hi there!"})
    assert resp.status_code == 201
    rule_id = resp.json()["rule_id"]

    doc = await test_db.rules.find_one({"rule_id": rule_id})
    assert doc is not None
    assert doc["keyword"] == "HELLO"
    assert doc["dm_message"] == "Hi there!"


async def test_create_rule_empty_keyword_rejected(app_client):
    resp = await app_client.post("/rules", json={"keyword": "", "dm_message": "msg"})
    assert resp.status_code == 422


async def test_create_rule_whitespace_only_keyword_rejected(app_client):
    resp = await app_client.post("/rules", json={"keyword": "   ", "dm_message": "msg"})
    assert resp.status_code == 422


async def test_create_rule_empty_message_rejected(app_client):
    resp = await app_client.post("/rules", json={"keyword": "PRICE", "dm_message": ""})
    assert resp.status_code == 422


async def test_create_multiple_rules(app_client):
    r1 = await app_client.post("/rules", json={"keyword": "PROMO", "dm_message": "Promo info"})
    r2 = await app_client.post("/rules", json={"keyword": "SALE", "dm_message": "Sale info"})
    assert r1.status_code == 201
    assert r2.status_code == 201
    # Rule IDs must be distinct
    assert r1.json()["rule_id"] != r2.json()["rule_id"]


async def test_create_rule_keyword_stripped(app_client, test_db):
    """Leading/trailing whitespace in keyword is stripped before storing."""
    resp = await app_client.post("/rules", json={"keyword": "  TRIM  ", "dm_message": "msg"})
    assert resp.status_code == 201
    rule_id = resp.json()["rule_id"]
    doc = await test_db.rules.find_one({"rule_id": rule_id})
    assert doc["keyword"] == "TRIM"
