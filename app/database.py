from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase
from pymongo import ASCENDING

from .config import get_settings

# Module-level client; can be reset to None in tests
_client: AsyncIOMotorClient | None = None


def get_client() -> AsyncIOMotorClient:
    global _client
    if _client is None:
        settings = get_settings()
        _client = AsyncIOMotorClient(settings.mongodb_url)
    return _client


def get_db() -> AsyncIOMotorDatabase:
    settings = get_settings()
    return get_client()[settings.database_name]


async def create_indexes() -> None:
    db = get_db()

    # rules: unique rule_id
    await db.rules.create_index("rule_id", unique=True)

    # events: unique event_id — enforces atomic deduplication
    await db.events.create_index("event_id", unique=True)

    # deliveries: unique (user_id, rule_id) — prevents duplicate DMs
    await db.deliveries.create_index(
        [("user_id", ASCENDING), ("rule_id", ASCENDING)],
        unique=True,
        name="user_rule_unique",
    )
    # deliveries: compound index for worker polling
    await db.deliveries.create_index(
        [("status", ASCENDING), ("retry_after", ASCENDING)],
        name="status_retry",
    )
    # deliveries: unique delivery_id for direct lookups
    await db.deliveries.create_index("delivery_id", unique=True)


async def close_client() -> None:
    global _client
    if _client is not None:
        _client.close()
        _client = None
