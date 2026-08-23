"""Package initialisation: environment and path anchoring.

Imported automatically before any `backend.*` submodule, because Python
imports parent packages first. That makes this the one reliable place to load
the .env: several submodules read environment variables at *import* time
(nodes.MODEL_NAME, vector_store.EMBEDDING_MODEL, linter.REPOSITORY_STORAGE_ROOT,
routing.MAX_AGENT_RETRIES), so an entry point that forgot to call load_dotenv()
before importing them would silently get fallback defaults instead.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

# backend/__init__.py -> backend/ -> project root
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Explicit path rather than dotenv's search-upward-from-CWD default: uvicorn may
# be launched from anywhere, and a missed .env fails silently.
load_dotenv(PROJECT_ROOT / ".env")


def resolve_project_path(value: str) -> Path:
    """Resolve a configured path against the project root rather than the CWD.

    Relative paths in .env (./storage/repositories, ./debugger.db) would
    otherwise land wherever the process happened to be started from, so the
    same config would point at different directories under uvicorn than under
    the smoke test -- with no error, just a silently empty repository.
    """
    path = Path(value).expanduser()
    if path.is_absolute():
        return path.resolve()
    return (PROJECT_ROOT / path).resolve()


__all__ = ["PROJECT_ROOT", "resolve_project_path"]
