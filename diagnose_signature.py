"""
Local diagnostic script for LinkPlease Automation webhook signature verification.

Prints signature metrics and comparison results without printing any secret information.

Usage:
    python diagnose_signature.py [<received_signature_header>]
"""
import hashlib
import hmac
import os
import sys
from dotenv import load_dotenv

load_dotenv()


def fingerprint(val: str) -> str:
    return hashlib.sha256(val.encode("utf-8")).hexdigest()[:8]


def main():
    api_key = os.getenv("PSEUDOGRAM_API_KEY", "").strip().strip('"').strip("'")

    # Standard webhook payload
    body = (
        b'{"event_id":"evt_manual_test_001","event_type":"comment.created",'
        b'"sent_at":"2026-08-10T09:14:22.481Z","data":{"comment_id":"cmt_manual_001",'
        b'"post_id":"post_manual_001","text":"PRICE please \ud83d\ude4f",'
        b'"created_at":"2026-08-10T09:14:21.900Z","from":{"user_id":"usr_manual_001",'
        b'"username":"test_user"}}}'
    )

    # Calculate expected signature
    expected_hex = hmac.new(
        api_key.encode("utf-8"),
        body,
        hashlib.sha256,
    ).hexdigest()

    # Extract received signature if provided as argument, otherwise default to matching expected
    received_header = sys.argv[1] if len(sys.argv) > 1 else f"sha256={expected_hex}"

    # Parse received signature
    received_hex = received_header
    if received_header.lower().startswith("sha256="):
        received_hex = received_header[7:]
    received_hex = received_hex.strip()

    matches = hmac.compare_digest(
        expected_hex.lower(), received_hex.lower()
    )

    # Print ONLY the requested metrics
    print(f"API key length: {len(api_key)}")
    print(f"API key fingerprint: {fingerprint(api_key)}")
    print(f"body length: {len(body)}")
    print(f"received signature length: {len(received_hex)}")
    print(f"expected signature length: {len(expected_hex)}")
    print(f"received signature fingerprint: {fingerprint(received_hex)}")
    print(f"expected signature fingerprint: {fingerprint(expected_hex)}")
    print(f"whether they match: {matches}")


if __name__ == "__main__":
    main()
