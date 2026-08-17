import hashlib
import hmac
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Request, Response
from pymongo.errors import DuplicateKeyError

from ..config import get_settings
from ..database import get_db
from ..services.matching import find_matching_rules

logger = logging.getLogger(__name__)
router = APIRouter()

_SIG_PREFIX = "sha256="


def _key_fingerprint(key: str) -> str:
    """Return first 8 chars of SHA-256 hash of the key string — safe to log."""
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:8]


def _compute_hmac(key_bytes: bytes, body: bytes) -> str:
    """Compute HMAC-SHA256 hex digest."""
    return hmac.new(key_bytes, body, hashlib.sha256).hexdigest()


def _verify_signature(raw_body: bytes, header: str, api_key: str) -> tuple[bool, str]:
    """
    Verify HMAC-SHA256 webhook signature using constant-time comparison.

    Expected header format: 'sha256=<hex-digest>'
    The HMAC key is the PseudoGram API key.
    The message is the exact raw request body bytes (not re-serialized JSON).

    To handle potential key-format differences with PseudoGram, tries multiple
    key derivations:
      1. Full API key as UTF-8 bytes
      2. Only the hex-token suffix (after the last dot) as UTF-8
      3. The hex-token suffix decoded from hex to raw bytes

    Returns (is_valid, reason_code) for safe diagnostic logging (NO SECRETS LOGGED).
    """
    cleaned_api_key = (api_key or "").strip().strip('"').strip("'")
    if not cleaned_api_key:
        return False, "missing_api_key"

    header_clean = (header or "").strip()
    if not header_clean:
        return False, "missing_signature_header"

    if not header_clean.lower().startswith(_SIG_PREFIX):
        return False, "malformed_signature_prefix"

    provided_hex = header_clean[len(_SIG_PREFIX):].strip().lower()
    if not provided_hex:
        return False, "empty_signature_hex"

    # Build candidate HMAC keys to try
    key_candidates: list[tuple[str, bytes]] = [
        ("full_key_utf8", cleaned_api_key.encode("utf-8")),
    ]

    # If the key has a dot, also try the suffix portion
    if "." in cleaned_api_key:
        suffix = cleaned_api_key.rsplit(".", 1)[1]
        key_candidates.append(("suffix_utf8", suffix.encode("utf-8")))
        try:
            key_candidates.append(("suffix_hex_decoded", bytes.fromhex(suffix)))
        except ValueError:
            pass  # suffix is not valid hex

    # Try each candidate
    for key_name, key_bytes in key_candidates:
        expected_hex = _compute_hmac(key_bytes, raw_body)
        if hmac.compare_digest(expected_hex.lower(), provided_hex):
            if key_name != "full_key_utf8":
                logger.info(
                    "Signature matched using key derivation '%s' (api_key_len=%d, body_bytes=%d)",
                    key_name, len(cleaned_api_key), len(raw_body),
                )
            return True, f"valid:{key_name}"

    # None matched — log diagnostics (safe: only lengths and fingerprints)
    primary_expected = _compute_hmac(cleaned_api_key.encode("utf-8"), raw_body)
    logger.warning(
        "Webhook signature mismatch: "
        "body_bytes=%d, api_key_len=%d, api_key_fp=%s, "
        "received_hex_len=%d, received_hex_head=%s, "
        "expected_hex_len=%d, expected_hex_head=%s",
        len(raw_body),
        len(cleaned_api_key),
        _key_fingerprint(cleaned_api_key),
        len(provided_hex),
        provided_hex[:8],
        len(primary_expected),
        primary_expected[:8],
    )
    return False, "signature_mismatch"


def _extract_comment_fields(payload: dict[str, Any]) -> tuple[str | None, str, str | None]:
    """
    Extract (comment_id, text, user_id) from a webhook payload.
    Handles fields at the top level or nested under 'data'.
    """
    # Some webhooks nest fields under 'data', others put them top-level
    data: dict[str, Any] = payload.get("data") or {}

    comment_id = data.get("comment_id") or payload.get("comment_id")
    text: str = data.get("text") or payload.get("text") or ""

    from_info = data.get("from") or payload.get("from")
    user_id: str | None = from_info.get("user_id") if isinstance(from_info, dict) else None

    return comment_id, text, user_id


@router.post("/webhook", status_code=200)
async def receive_webhook(
    request: Request,
    x_pseudogram_signature: str | None = Header(
        None,
        alias="X-PseudoGram-Signature",
        description="HMAC-SHA256 signature of the raw request body using PSEUDOGRAM_API_KEY (format: sha256=<hex>)",
    ),
) -> dict[str, str]:
    """
    Receive a comment webhook from PseudoGram.

    Must return HTTP 200 within 5 seconds.
    All slow work (DM sending, retries) is handled by the background worker.
    """
    settings = get_settings()
    raw_body = await request.body()

    # Diagnostic logging (Safe: NO secrets, keys, or complete signatures logged)
    logger.info(
        "Received POST /webhook: body_bytes=%d, verify_sig=%s, api_key_len=%d, api_key_fp=%s",
        len(raw_body),
        settings.verify_webhook_signature,
        len(settings.pseudogram_api_key),
        _key_fingerprint(settings.pseudogram_api_key),
    )

    # --- Part B: Signature verification ---
    if settings.verify_webhook_signature:
        sig_header = (
            x_pseudogram_signature
            or request.headers.get("X-PseudoGram-Signature")
            or request.headers.get("x-pseudogram-signature")
            or ""
        )

        is_valid, reason = _verify_signature(
            raw_body=raw_body,
            header=sig_header,
            api_key=settings.pseudogram_api_key,
        )

        logger.info(
            "Signature check: has_header=%s, is_valid=%s, reason=%s",
            bool(sig_header),
            is_valid,
            reason,
        )

        if not is_valid:
            raise HTTPException(status_code=401, detail="Invalid or missing webhook signature")

    try:
        payload: dict[str, Any] = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Request body is not valid JSON")

    event_id: str | None = payload.get("event_id")
    event_type: str | None = payload.get("event_type")

    if not event_id or not event_type:
        raise HTTPException(status_code=400, detail="Missing required fields: event_id, event_type")

    db = get_db()

    # --- Atomic event deduplication ---
    # Unique index on event_id ensures only one insert succeeds across concurrent requests.
    try:
        await db.events.insert_one(
            {
                "event_id": event_id,
                "event_type": event_type,
                "received_at": datetime.now(timezone.utc),
                "processed": False,
            }
        )
    except DuplicateKeyError:
        # Duplicate event_id — acknowledge but do not re-process.
        # Do NOT increment duplicates_blocked here: this is event dedup, not DM dedup.
        logger.debug("Duplicate event %s ignored", event_id)
        return {"status": "accepted"}

    # --- Dispatch ---
    if event_type == "comment.created":
        await _handle_comment_created(payload, db)
    elif event_type == "comment.deleted":
        await _handle_comment_deleted(payload, db)
    else:
        logger.debug("Ignoring unknown event type: %s", event_type)

    await db.events.update_one(
        {"event_id": event_id},
        {"$set": {"processed": True}},
    )

    return {"status": "accepted"}


async def _handle_comment_created(payload: dict[str, Any], db) -> None:
    comment_id, text, user_id = _extract_comment_fields(payload)

    if not comment_id or not user_id:
        logger.warning(
            "comment.created missing comment_id or user_id — skipping. payload keys: %s",
            list(payload.keys()),
        )
        return

    matching_rules = await find_matching_rules(text, db)
    if not matching_rules:
        return

    now = datetime.now(timezone.utc)

    for rule in matching_rules:
        rule_id: str = rule["rule_id"]

        # Generate attempt-specific idempotency key.
        # This key is reused on 500 retries (same attempt).
        # A new key is only generated by the reconciliation worker after confirmed terminal failure.
        attempt_id = str(uuid.uuid4())

        try:
            await db.deliveries.insert_one(
                {
                    "delivery_id": str(uuid.uuid4()),
                    "user_id": user_id,
                    "rule_id": rule_id,
                    "comment_id": comment_id,
                    "message": rule["dm_message"],
                    "status": "pending",
                    "dm_id": None,
                    "idempotency_key": f"attempt:{attempt_id}",
                    "attempts": 0,
                    "reconciliation_attempts": 0,
                    "retry_after": None,
                    "created_at": now,
                    "updated_at": now,
                }
            )
            logger.debug(
                "Delivery queued: user=%s rule=%s comment=%s", user_id, rule_id, comment_id
            )
        except DuplicateKeyError:
            # (user_id, rule_id) unique index — this user already has/had a delivery for this rule.
            # This is the DM-level duplicate: increment duplicates_blocked.
            await db.app_stats.update_one(
                {"_id": "counters"},
                {"$inc": {"duplicates_blocked": 1}},
                upsert=True,
            )
            logger.debug(
                "Duplicate DM blocked: user=%s rule=%s", user_id, rule_id
            )


async def _handle_comment_deleted(payload: dict[str, Any], db) -> None:
    data: dict[str, Any] = payload.get("data") or {}
    comment_id = data.get("comment_id") or payload.get("comment_id")
    if not comment_id:
        return

    # Cancel only pending deliveries — accepted/delivered cannot be undone
    result = await db.deliveries.update_many(
        {"comment_id": comment_id, "status": "pending"},
        {"$set": {"status": "cancelled", "updated_at": datetime.now(timezone.utc)}},
    )
    if result.modified_count:
        logger.info(
            "Cancelled %d pending delivery/ies for deleted comment %s",
            result.modified_count,
            comment_id,
        )
