import logging
from dataclasses import dataclass
from enum import Enum
from typing import Optional

import httpx

from ..config import get_settings

logger = logging.getLogger(__name__)


class SendStatus(Enum):
    ACCEPTED = "accepted"           # 202 — queued by PseudoGram (not yet delivered)
    TRANSIENT_FAILURE = "transient_failure"   # 500 / network error — retry with same idempotency key
    PERMANENT_FAILURE = "permanent_failure"   # 400 — do not retry
    RATE_LIMITED = "rate_limited"             # 429 — wait and retry


@dataclass
class SendResult:
    status: SendStatus
    dm_id: Optional[str] = None
    retry_after_seconds: Optional[float] = None
    http_status: int = 0


async def send_dm(
    client: httpx.AsyncClient,
    *,
    recipient_user_id: str,
    message: str,
    comment_id: str,
    idempotency_key: str,
) -> SendResult:
    """
    Call POST /v1/dm/send on PseudoGram.
    Returns a structured result; never raises on API errors.
    """
    settings = get_settings()

    try:
        response = await client.post(
            f"{settings.pseudogram_base_url}/v1/dm/send",
            json={
                "recipient_user_id": recipient_user_id,
                "message": message,
                "comment_id": comment_id,
            },
            headers={
                "X-API-Key": settings.pseudogram_api_key,
                "Idempotency-Key": idempotency_key,
                "Content-Type": "application/json",
            },
            timeout=30.0,
        )
    except httpx.RequestError as exc:
        logger.warning("DM send network error: %s", exc)
        return SendResult(status=SendStatus.TRANSIENT_FAILURE)

    if response.status_code == 202:
        data = response.json()
        return SendResult(
            status=SendStatus.ACCEPTED,
            dm_id=data.get("dm_id"),
            http_status=202,
        )

    if response.status_code == 429:
        # Honor the Retry-After header; default to 60s if absent
        retry_after = float(response.headers.get("Retry-After", "60"))
        logger.warning("DM API 429: retry_after=%.1fs", retry_after)
        return SendResult(
            status=SendStatus.RATE_LIMITED,
            retry_after_seconds=retry_after,
            http_status=429,
        )

    if response.status_code >= 500:
        logger.warning("DM API transient error HTTP %d", response.status_code)
        return SendResult(status=SendStatus.TRANSIENT_FAILURE, http_status=response.status_code)

    # 400 or any other 4xx — permanent failure, do not retry
    logger.error("DM API permanent error HTTP %d: %s", response.status_code, response.text[:300])
    return SendResult(status=SendStatus.PERMANENT_FAILURE, http_status=response.status_code)


async def get_dm_status(client: httpx.AsyncClient, dm_id: str) -> Optional[str]:
    """
    Call GET /v1/dm/{dm_id}.
    Returns 'queued', 'delivered', 'failed', or None on error.
    """
    settings = get_settings()

    try:
        response = await client.get(
            f"{settings.pseudogram_base_url}/v1/dm/{dm_id}",
            headers={"X-API-Key": settings.pseudogram_api_key},
            timeout=30.0,
        )
        if response.status_code == 200:
            return response.json().get("status")
        logger.warning("DM status check %s → HTTP %d", dm_id, response.status_code)
        return None
    except Exception as exc:
        logger.warning("DM status check error for %s: %s", dm_id, exc)
        return None
