from fastapi import FastAPI

from measurements.router import router as measurements_router
from prescriptions.router import router as prescriptions_router

app = FastAPI(title="BP Tracker API")

app.include_router(measurements_router)
app.include_router(prescriptions_router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
