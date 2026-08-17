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


def _verify_signature(raw_body: bytes, header: str, api_key: str) -> bool:
    """
    Verify HMAC-SHA256 webhook signature using constant-time comparison.

    Expected header format: 'sha256=<hex-digest>'
    The HMAC key is the PseudoGram API key.
    The message is the exact raw request body bytes (not re-serialized JSON).
    """
    header = (header or "").strip()
    if not header.startswith(_SIG_PREFIX) or not api_key:
        return False
    provided_hex = header[len(_SIG_PREFIX):]
    expected_hex = hmac.new(
        api_key.encode("utf-8"),
        raw_body,
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected_hex, provided_hex)


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

    # --- Part B: Signature verification ---
    if settings.verify_webhook_signature:
        sig_header = x_pseudogram_signature or request.headers.get("X-PseudoGram-Signature", "")
        if not _verify_signature(raw_body, sig_header, settings.pseudogram_api_key):
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
