"""
Shared test configuration and fixtures.

Environment variables are set at module load time so that app.config reads
the correct test values before any lru_cache is populated.
"""
import hashlib
import hmac
import json
import os
from unittest.mock import patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from motor.motor_asyncio import AsyncIOMotorClient

# -----------------------------------------------------------------------
# Test environment — set BEFORE any app imports
# -----------------------------------------------------------------------
TEST_API_KEY = "test_key_linkplease_unit_abc123"
TEST_MONGODB_URL = os.environ.get("TEST_MONGODB_URL", "mongodb://localhost:27017")
TEST_DATABASE_NAME = "linkplease_test"
TEST_BASE_URL = "https://pseudogram-api.onrender.com"

os.environ["PSEUDOGRAM_API_KEY"] = TEST_API_KEY
os.environ["MONGODB_URL"] = TEST_MONGODB_URL
os.environ["DATABASE_NAME"] = TEST_DATABASE_NAME
os.environ["PSEUDOGRAM_BASE_URL"] = TEST_BASE_URL


# -----------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------

def make_signature(body: bytes, key: str = TEST_API_KEY) -> str:
    """Return 'sha256=<hex>' signature for a raw body."""
    digest = hmac.new(key.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def signed_webhook(payload: dict, key: str = TEST_API_KEY) -> tuple[bytes, dict]:
    """
    Serialize payload to JSON bytes and return (raw_body, headers) with valid signature.
    Uses compact separators to match typical webhook senders.
    """
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "X-PseudoGram-Signature": make_signature(raw, key),
    }
    return raw, headers


# -----------------------------------------------------------------------
# Fixtures
# -----------------------------------------------------------------------

@pytest_asyncio.fixture
async def test_db():
    """
    Provide a clean test database for each test.
    Drops all collections before yielding, recreates indexes.
    """
    import app.database as db_mod
    from app.config import get_settings

    # Ensure settings cache uses test values
    get_settings.cache_clear()
    # Reset the module-level client so it is re-created from test settings
    db_mod._client = None

    client = AsyncIOMotorClient(TEST_MONGODB_URL)
    db = client[TEST_DATABASE_NAME]

    for name in await db.list_collection_names():
        await db.drop_collection(name)

    # Point the app's DB module at the test client so create_indexes() works
    db_mod._client = client
    from app.database import create_indexes
    await create_indexes()

    yield db

    # Teardown
    for name in await db.list_collection_names():
        await db.drop_collection(name)
    client.close()
    db_mod._client = None


@pytest_asyncio.fixture
async def app_client(test_db):
    """
    HTTP test client for the FastAPI app.
    Background workers are replaced with instant no-ops so they don't interfere.
    """
    async def _noop():
        pass

    with patch("app.main.run_dm_worker", new=_noop), \
         patch("app.main.run_reconciliation_worker", new=_noop):
        from app.main import app
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            yield client
