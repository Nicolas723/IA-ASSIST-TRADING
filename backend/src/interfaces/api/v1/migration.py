import asyncio
import uuid

from fastapi import APIRouter
from pydantic import BaseModel, Field

router = APIRouter()

# Simple in-memory queue for demo purposes
migration_queue: asyncio.Queue[dict[str, object]] = asyncio.Queue()


class MigrationResponse(BaseModel):
    status: str = Field(..., description="Queue status", examples=["queued"])
    task_id: str = Field(..., description="Unique migration task identifier")


@router.post(
    "/migration/start", response_model=MigrationResponse, summary="Trigger migration process"
)
async def start_migration() -> MigrationResponse:
    """Enqueue a migration job.
    In a production system this would enqueue a job in a persistent broker
    (e.g., Redis RQ, Celery, etc.). Here we simply place a dict into an
    asyncio.Queue that the background consumer will process.
    """
    task_id = str(uuid.uuid4())
    await migration_queue.put({"task_id": task_id, "created_at": asyncio.get_event_loop().time()})
    return MigrationResponse(status="queued", task_id=task_id)


# Consumer started during app lifespan (see src/main.py)
async def _migration_worker() -> None:
    while True:
        job = await migration_queue.get()
        # Placeholder for real work - simulate with sleep
        await asyncio.sleep(2)  # simulate migration duration
        # Log completion
        from src.shared.logging.setup import get_logger

        logger = get_logger(__name__)
        logger.info("migration_completed", task_id=job["task_id"])
        migration_queue.task_done()
