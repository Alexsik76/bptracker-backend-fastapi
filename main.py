from fastapi import FastAPI

from auth.router import router as auth_router
from auth.webauthn import router as webauthn_router
from measurements.router import router as measurements_router
from prescriptions.router import router as prescriptions_router
from reminders.router import router as reminders_router

app = FastAPI(title="BP Tracker API")

app.include_router(auth_router)
app.include_router(webauthn_router)
app.include_router(measurements_router)
app.include_router(prescriptions_router)
app.include_router(reminders_router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
