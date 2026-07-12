import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from auth.router import router as auth_router
from auth.webauthn import router as webauthn_router
from config import get_settings
from db import async_session_factory
from email_infra import get_email_sender
from email_infra.worker import run_email_outbox_worker
from measurements.router import router as measurements_router
from prescriptions.router import router as prescriptions_router
from reminders.router import router as reminders_router

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    worker_task = None
    if settings.email_outbox_worker_enabled:
        smtp_sender = get_email_sender(settings)
        worker_task = asyncio.create_task(
            run_email_outbox_worker(
                session_factory=async_session_factory,
                smtp_sender=smtp_sender,
                settings=settings,
            )
        )
        logger.info("Email outbox worker background task started.")

    try:
        yield
    finally:
        if worker_task is not None:
            logger.info("Cancelling email outbox worker background task...")
            worker_task.cancel()
            try:
                await worker_task
            except asyncio.CancelledError:
                pass
            logger.info("Email outbox worker background task stopped.")


app = FastAPI(title="BP Tracker API", lifespan=lifespan)

app.include_router(auth_router)
app.include_router(webauthn_router)
app.include_router(measurements_router)
app.include_router(prescriptions_router)
app.include_router(reminders_router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
