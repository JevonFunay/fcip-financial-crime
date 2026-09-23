from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings
from app.routers import alerts, audit, auth, cases, detection, ingestion, overview, transactions

app = FastAPI(title="Financial Crime Intelligence Platform API")
app.include_router(auth.router, prefix="/auth", tags=["auth"])
app.include_router(audit.router, prefix="/audit", tags=["audit"])
app.include_router(overview.router, prefix="/overview", tags=["overview"])
app.include_router(ingestion.router, prefix="/ingestion", tags=["ingestion"])
app.include_router(transactions.router, prefix="/transactions", tags=["transactions"])
app.include_router(detection.router, prefix="/detection", tags=["detection"])
app.include_router(alerts.router, prefix="/alerts", tags=["alerts"])
app.include_router(cases.router, prefix="/cases", tags=["cases"])

# The Vite dev server runs on a different origin than the API, so the browser
# needs an explicit CORS allow (CORS_ORIGINS in .env).
app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
