"""
FastAPI application entry point.
Importing anything under `backend` runs backend/__init__.py first, which loads
the .env -- so no load_dotenv() call is needed here, and modules that read
environment variables at import time see real values.
"""

import logging

from fastapi import FastAPI

from backend.api.routes import router
logging.basicConfig(
    level = logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
)
app = FastAPI(
    title = "PatchPilot",
    description="Upload a repository, describe the bug, and review the patch the agent proposes.",
    version="0.1.0",
)

app.include_router(router)

@app.get("/health",summary="Liveness check")
def health() -> dict[str, str]:
    return {"status":"ok"}