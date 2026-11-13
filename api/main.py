"""FastAPI app entry point."""

from fastapi import FastAPI

app = FastAPI(title="Stitch API")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
