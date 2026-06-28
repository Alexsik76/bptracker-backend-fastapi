from fastapi import FastAPI

app = FastAPI(title="BP Tracker API")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}