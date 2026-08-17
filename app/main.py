import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from .database import close_client, create_indexes
from .routes import rules, stats, webhook
from .workers.dm_worker import run_dm_worker
from .workers.reconciliation_worker import run_reconciliation_worker

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: ensure indexes exist, launch background workers
    await create_indexes()

    dm_task = asyncio.create_task(run_dm_worker(), name="dm_worker")
    recon_task = asyncio.create_task(run_reconciliation_worker(), name="reconciliation_worker")

    logger.info("LinkPlease backend started")
    yield

    # Shutdown: cancel workers gracefully, close DB connection
    dm_task.cancel()
    recon_task.cancel()
    try:
        await asyncio.gather(dm_task, recon_task, return_exceptions=True)
    finally:
        pass

    await close_client()
    logger.info("LinkPlease backend stopped")


app = FastAPI(
    title="LinkPlease Automation",
    version="1.0.0",
    description="Receives PseudoGram comment webhooks and sends DMs based on keyword rules.",
    lifespan=lifespan,
)

app.include_router(rules.router)
app.include_router(webhook.router)
app.include_router(stats.router)
