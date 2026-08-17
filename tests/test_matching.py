"""Tests for keyword matching logic."""
import pytest
from datetime import datetime, timezone

from app.services.matching import find_matching_rules


async def _insert_rule(db, keyword: str, message: str = "msg") -> str:
    import uuid
    rule_id = str(uuid.uuid4())
    await db.rules.insert_one({
        "rule_id": rule_id,
        "keyword": keyword,
        "dm_message": message,
        "created_at": datetime.now(timezone.utc),
    })
    return rule_id


async def test_exact_match(test_db):
    await _insert_rule(test_db, "PRICE")
    matches = await find_matching_rules("PRICE", test_db)
    assert len(matches) == 1
    assert matches[0]["keyword"] == "PRICE"


async def test_case_insensitive_lower(test_db):
    await _insert_rule(test_db, "PRICE")
    matches = await find_matching_rules("price", test_db)
    assert len(matches) == 1


async def test_case_insensitive_mixed(test_db):
    await _insert_rule(test_db, "PRICE")
    matches = await find_matching_rules("What is the Price?", test_db)
    assert len(matches) == 1


async def test_substring_match(test_db):
    await _insert_rule(test_db, "PRICE")
    matches = await find_matching_rules("Tell me the PRICE please!", test_db)
    assert len(matches) == 1


async def test_keyword_embedded_in_word(test_db):
    """Keyword 'RICE' should match text containing 'RICE' even inside 'PRICE'."""
    await _insert_rule(test_db, "RICE")
    matches = await find_matching_rules("I want PRICE info", test_db)
    assert len(matches) == 1


async def test_no_match(test_db):
    await _insert_rule(test_db, "PRICE")
    matches = await find_matching_rules("Hello world", test_db)
    assert len(matches) == 0


async def test_empty_text_no_match(test_db):
    await _insert_rule(test_db, "PRICE")
    matches = await find_matching_rules("", test_db)
    assert len(matches) == 0


async def test_multiple_rules_all_match(test_db):
    await _insert_rule(test_db, "PRICE")
    await _insert_rule(test_db, "PROMO")
    matches = await find_matching_rules("I want PRICE and PROMO", test_db)
    assert len(matches) == 2


async def test_multiple_rules_partial_match(test_db):
    await _insert_rule(test_db, "PRICE")
    await _insert_rule(test_db, "PROMO")
    matches = await find_matching_rules("Only PRICE here", test_db)
    assert len(matches) == 1
    assert matches[0]["keyword"] == "PRICE"


async def test_no_rules_no_match(test_db):
    matches = await find_matching_rules("PRICE PROMO SALE", test_db)
    assert len(matches) == 0


async def test_keyword_rule_case_stored_uppercase(test_db):
    """Rule keyword stored as uppercase should match lowercase comment."""
    await _insert_rule(test_db, "AVAILABLE")
    matches = await find_matching_rules("Is it available?", test_db)
    assert len(matches) == 1


async def test_keyword_stored_lowercase_matches_uppercase_comment(test_db):
    await _insert_rule(test_db, "available")
    matches = await find_matching_rules("AVAILABLE NOW!", test_db)
    assert len(matches) == 1
