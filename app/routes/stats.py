from fastapi import APIRouter

from ..database import get_db

router = APIRouter()


@router.get("/stats")
async def get_stats() -> dict:
    db = get_db()

    sent = await db.deliveries.count_documents({"status": "delivered"})
    failed = await db.deliveries.count_documents({"status": "failed"})
    queued = await db.deliveries.count_documents(
        {"status": {"$in": ["pending", "claimed", "accepted"]}}
    )

    # duplicates_blocked: persisted counter — only incremented when a rule match
    # is found but the (user_id, rule_id) unique constraint already exists.
    # NOT incremented for duplicate event_id or unknown event types.
    stats_doc = await db.app_stats.find_one({"_id": "counters"})
    duplicates_blocked = stats_doc.get("duplicates_blocked", 0) if stats_doc else 0

    return {
        "sent": sent,
        "failed": failed,
        "queued": queued,
        "duplicates_blocked": duplicates_blocked,
    }
