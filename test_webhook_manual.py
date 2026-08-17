"""
Local manual testing script for POST /webhook.

Usage:
    python test_webhook_manual.py
"""
import hashlib
import hmac
import json
import os
import urllib.error
import urllib.request
from dotenv import load_dotenv

load_dotenv()

API_KEY = os.getenv(
    "PSEUDOGRAM_API_KEY",
    "c2F1cmFiaGt1bWFyMDg4NDNAZ21haWwuY29t.180b5b787f94b378e818",
)
URL = os.getenv("TEST_URL", "http://127.0.0.1:8000/webhook")

payload = {
    "event_id": "evt_manual_test_001",
    "event_type": "comment.created",
    "sent_at": "2026-08-10T09:14:22.481Z",
    "data": {
        "comment_id": "cmt_manual_001",
        "post_id": "post_manual_001",
        "text": "PRICE please 🙏",
        "created_at": "2026-08-10T09:14:21.900Z",
        "from": {
            "user_id": "usr_manual_001",
            "username": "test_user",
        },
    },
}

# 1. Create exact raw JSON bytes
raw_body = json.dumps(payload, separators=(",", ":")).encode("utf-8")

# 2. Calculate HMAC-SHA256 signature using PSEUDOGRAM_API_KEY
signature_hex = hmac.new(
    API_KEY.encode("utf-8"),
    raw_body,
    hashlib.sha256,
).hexdigest()
signature_header = f"sha256={signature_hex}"

print(f"Target URL: {URL}")
print(f"Using API Key: {API_KEY[:15]}...")
print(f"X-PseudoGram-Signature: {signature_header}")
print(f"Payload Body: {raw_body.decode('utf-8')}\n")

# 3. Send POST request with raw_body and X-PseudoGram-Signature header
req = urllib.request.Request(
    URL,
    data=raw_body,
    headers={
        "Content-Type": "application/json",
        "X-PseudoGram-Signature": signature_header,
    },
    method="POST",
)

try:
    with urllib.request.urlopen(req) as resp:
        print(f"SUCCESS! HTTP Status: {resp.status}")
        print(f"Response Body: {resp.read().decode('utf-8')}")
except urllib.error.HTTPError as e:
    print(f"FAILED! HTTP Status: {e.code}")
    print(f"Response Body: {e.read().decode('utf-8')}")
