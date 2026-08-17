import asyncio
import logging
import uuid
from datetime import datetime, timezone

import httpx

from ..database import get_db
from ..services.dm import get_dm_status

logger = logging.getLogger(__name__)

RECONCILIATION_INTERVAL = 10.0  # seconds between full reconciliation runs
MAX_RECONCILIATION_RETRIES = 3  # max times to re-enqueue after confirmed failure


async def run_reconciliation_worker() -> None:
    """
    Periodically checks all accepted DMs against PseudoGram and updates their status.

    When PseudoGram confirms 'delivered': status → delivered (increments /stats sent).
    When PseudoGram confirms 'failed':
      - If under MAX_RECONCILIATION_RETRIES: re-enqueue with a NEW idempotency key
        (the previous attempt is definitively dead; PseudoGram needs a fresh request).
      - Otherwise: status → failed permanently.
    """
    logger.info("Reconciliation worker started")

    async with httpx.AsyncClient() as client:
        while True:
            try:
                await _reconcile_once(client)
            except asyncio.CancelledError:
                logger.info("Reconciliation worker cancelled")
                raise
            except Exception as exc:
                logger.exception("Error in reconciliation worker: %s", exc)

            await asyncio.sleep(RECONCILIATION_INTERVAL)

    logger.info("Reconciliation worker stopped")


async def _reconcile_once(client: httpx.AsyncClient) -> None:
    db = get_db()
    now = datetime.now(timezone.utc)

    # Fetch accepted deliveries with a known dm_id (limit batch size to avoid huge cursors)
    accepted = await db.deliveries.find(
        {"status": "accepted", "dm_id": {"$ne": None}}
    ).to_list(length=500)

    for delivery in accepted:
        delivery_id = delivery["delivery_id"]
        dm_id = delivery["dm_id"]

        status = await get_dm_status(client, dm_id)

        if status == "delivered":
            await db.deliveries.update_one(
                {"delivery_id": delivery_id},
                {"$set": {"status": "delivered", "updated_at": now}},
            )
            logger.info("Delivery %s confirmed delivered (dm_id=%s)", delivery_id, dm_id)

        elif status == "failed":
            recon_attempts = delivery.get("reconciliation_attempts", 0) + 1

            if recon_attempts >= MAX_RECONCILIATION_RETRIES:
                await db.deliveries.update_one(
                    {"delivery_id": delivery_id},
                    {
                        "$set": {
                            "status": "failed",
                            "reconciliation_attempts": recon_attempts,
                            "updated_at": now,
                        }
                    },
                )
                logger.error(
                    "Delivery %s permanently failed after %d reconciliation retries",
                    delivery_id,
                    recon_attempts,
                )
            else:
                # New idempotency key — this is a genuinely new send attempt.
                # The previous attempt was confirmed dead by PseudoGram.
                new_idempotency_key = f"attempt:{uuid.uuid4()}"
                await db.deliveries.update_one(
                    {"delivery_id": delivery_id},
                    {
                        "$set": {
                            "status": "pending",
                            "dm_id": None,
                            "idempotency_key": new_idempotency_key,
                            "attempts": 0,          # reset send-attempt counter for new attempt
                            "reconciliation_attempts": recon_attempts,
                            "retry_after": None,
                            "updated_at": now,
                        }
                    },
                )
                logger.warning(
                    "Re-enqueued delivery %s with new idempotency key "
                    "(reconciliation attempt %d/%d)",
                    delivery_id,
                    recon_attempts,
                    MAX_RECONCILIATION_RETRIES,
                )

        # status == "queued" or None: still waiting, no action needed
