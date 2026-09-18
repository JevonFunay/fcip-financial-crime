from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.routers import auth, ingestion

app = FastAPI(title="Financial Crime Intelligence Platform API")
app.include_router(auth.router, prefix="/auth", tags=["auth"])
app.include_router(ingestion.router, prefix="/ingestion", tags=["ingestion"])

# Dev-only: the Vite dev server runs on a different origin (localhost:5173)
# than the API (localhost:8000), so the browser needs an explicit CORS allow.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
