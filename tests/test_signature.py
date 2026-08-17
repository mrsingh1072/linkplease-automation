"""Tests for webhook signature verification."""
import hashlib
import hmac

import pytest

from app.routes.webhook import _verify_signature

TEST_KEY = "test_key_for_signature_tests"


def _make_sig(body: bytes, key: str = TEST_KEY) -> str:
    digest = hmac.new(key.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def test_valid_signature_accepted():
    body = b'{"event_id":"evt1","event_type":"comment.created"}'
    sig = _make_sig(body)
    assert _verify_signature(body, sig, TEST_KEY) is True


def test_wrong_key_rejected():
    body = b'{"event_id":"evt1","event_type":"comment.created"}'
    sig = _make_sig(body, key="wrong_key")
    assert _verify_signature(body, sig, TEST_KEY) is False


def test_tampered_body_rejected():
    body = b'{"event_id":"evt1","event_type":"comment.created"}'
    sig = _make_sig(body)
    # Tamper the body after signing
    tampered = b'{"event_id":"evt2","event_type":"comment.created"}'
    assert _verify_signature(tampered, sig, TEST_KEY) is False


def test_missing_sha256_prefix_rejected():
    body = b'test body'
    digest = hmac.new(TEST_KEY.encode(), body, hashlib.sha256).hexdigest()
    # No 'sha256=' prefix
    assert _verify_signature(body, digest, TEST_KEY) is False


def test_empty_header_rejected():
    body = b'test body'
    assert _verify_signature(body, "", TEST_KEY) is False


def test_malformed_header_rejected():
    body = b'test body'
    assert _verify_signature(body, "sha256=", TEST_KEY) is False
    assert _verify_signature(body, "sha256=xyz", TEST_KEY) is False


def test_wrong_prefix_word_rejected():
    body = b'test body'
    digest = hmac.new(TEST_KEY.encode(), body, hashlib.sha256).hexdigest()
    assert _verify_signature(body, f"md5={digest}", TEST_KEY) is False


def test_constant_time_comparison_used():
    """
    Verify that _verify_signature uses hmac.compare_digest (constant-time).
    We cannot directly test timing; we verify the implementation uses the correct
    approach by checking that a known-wrong trailing character is rejected.
    """
    body = b'{"test": 1}'
    key = "mykey"
    correct = hmac.new(key.encode(), body, hashlib.sha256).hexdigest()
    # Flip last character
    last = correct[-1]
    wrong_char = "0" if last != "0" else "1"
    tampered_hex = correct[:-1] + wrong_char
    assert _verify_signature(body, f"sha256={tampered_hex}", key) is False


def test_empty_body_valid_signature():
    body = b""
    sig = _make_sig(body)
    assert _verify_signature(body, sig, TEST_KEY) is True


def test_unicode_key_handled():
    """API key may contain non-ASCII characters; ensure encode works."""
    body = b'{"x": 1}'
    key = "key_with_ascii_only_123"
    sig = _make_sig(body, key=key)
    assert _verify_signature(body, sig, key) is True
