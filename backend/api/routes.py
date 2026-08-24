"""HTTP routes for the debugging platform."""

import logging
import os
import shutil
import stat
import tempfile
import zipfile
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, UploadFile, status

from backend.api.schemas import UploadResponse
from backend.rag.code_splitter import CodeSplitter
from backend.rag.repository_loader import RepositoryLoader
from backend.rag.vector_store import get_vector_store
from backend.tools.linter import (
    SAFE_REPOSITORY_ID,
    UnsafePathError,
    resolve_repository_root,
)

logger = logging.getLogger(__name__)

router = APIRouter()

MAX_UPLOAD_BYTES = int(os.getenv("MAX_REPOSITORY_SIZE_MB", "20")) * 1024 * 1024
# A zip bomb is small on disk and enormous once expanded, and extractall()
# enforces no limit of its own, so the expanded size needs a separate cap.

MAX_EXTRACTED_BYTES = MAX_UPLOAD_BYTES * 20
MAX_MEMBERS = 20_000
STREAM_CHUNK = 1024 *1024

async def _stream_to_temp(upload: UploadFile, limit: int) -> Path:
    """
    Write the upload to a temp file, aborting once it exceeds `limit`.
    Deliberately not `await upload.read()`: that pulls the entire body into
    memory, so an oversized upload is already a problem before any size check
    could run. Content-Length is client-supplied and cannot be trusted either,
    so the only honest limit is one counted while writing.
    """

    fd, tmp_name = tempfile.mkstemp(suffix=".zip")
    tmp = Path(tmp_name)
    written = 0
    try:
        with os.fdopen(fd, "wb") as out:
            while chunk := await upload.read(STREAM_CHUNK):
                written += len(chunk)
                if written > limit:
                    raise HTTPException(
                        status.HTTP_413_CONTENT_TOO_LARGE
                        ,
                        f"Archive exceeds the {limit // (1024*1024)} MB limit.",
                    )
                out.write(chunk)

    except Exception:
        tmp.unlink(missing_ok=True)
        raise
    return tmp


def _inspect_archive(zip_path: Path) -> None:
    """
    Reject archives that are corrupt, hostile, or oversized when expanded.
    Python's extractall() already neutralises classic zip slip -- it strips
    leading slashes and '..' components and writes symlink entries as plain
    files. What it does NOT do is limit the expanded size or the member count,
    and it silently *rewrites* hostile paths rather than refusing them, so
    '../../x' lands in a junk subdirectory instead of erroring. Checking here
    turns that into a clear 400 and keeps the guarantee ours.
    """
    try:
        with zipfile.ZipFile(zip_path) as zf:
            infos = zf.infolist()

            if len(infos) > MAX_MEMBERS:
                raise HTTPException(
                    status.HTTP_413_CONTENT_TOO_LARGE,
                    f"Archive has {len(infos)} entries, more than the {MAX_MEMBERS} allowed.",
                )

            total = sum(i.file_size for i in infos)
            if total > MAX_EXTRACTED_BYTES:
                raise HTTPException(
                    status.HTTP_413_CONTENT_TOO_LARGE,
                    f"Archive expands to {total // (1024 * 1024)} MB, over the "
                    f"{MAX_EXTRACTED_BYTES // (1024 * 1024)} MB limit (possible zip bomb).",
                )

            for info in infos:
                name = info.filename
                if name.startswith("/") or ".." in Path(name).parts:
                    raise HTTPException(
                        status.HTTP_400_BAD_REQUEST,
                        f"Archive entry escapes the repository root: {name!r}",
                    )
                if stat.S_ISLNK(info.external_attr >> 16):
                    raise HTTPException(
                        status.HTTP_400_BAD_REQUEST,
                        f"Archive contains a symlink, which is not supported: {name!r}",
                    )
    except zipfile.BadZipFile:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "Uploaded file is not a valid zip archive."
        )


def _flatten_single_root(path: Path) -> None:
    """Lift contents up when the archive wrapped everything in one directory.

    Compressing a folder in Finder, or GitHub's "Download ZIP", produces
    'myproject/...' inside the archive. Left alone, every source file sits one
    level below the repository root the tools resolve to, so target_file paths
    from the model would never match.
    """
    entries = [p for p in path.iterdir() if p.name != "__MACOSX"]
    if len(entries) != 1 or not entries[0].is_dir():
        return

    inner = entries[0]
    logger.info("Flattening single-root archive directory %r", inner.name)
    for item in list(inner.iterdir()):
        shutil.move(str(item), str(path / item.name))
    inner.rmdir()


@router.post(
    "/repositories/upload",
    response_model=UploadResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Upload and index a repository",
)
async def upload_repository(
    repository_id: str = Form(..., description="Identifier to store this repository under."),
    file: UploadFile = File(..., description="A .zip archive of the repository."),
) -> UploadResponse:
    """Accept a zipped repository, extract it safely, and index it for search."""
    if not SAFE_REPOSITORY_ID.match(repository_id):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "repository_id may contain only letters, digits, underscores and hyphens.",
        )

    if not (file.filename or "").lower().endswith(".zip"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Upload must be a .zip archive.")

    try:
        repo_root = resolve_repository_root(repository_id)
    except UnsafePathError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(e))

    tmp_zip = await _stream_to_temp(file, MAX_UPLOAD_BYTES)
    try:
        _inspect_archive(tmp_zip)

        # Replace any previous upload under this id. Destructive on purpose --
        # leaving old files behind would let the model target a file that is no
        # longer part of the repository.
        if repo_root.exists():
            shutil.rmtree(repo_root)
        repo_root.mkdir(parents=True, exist_ok=True)

        with zipfile.ZipFile(tmp_zip) as zf:
            zf.extractall(repo_root)

        shutil.rmtree(repo_root / "__MACOSX", ignore_errors=True)
        _flatten_single_root(repo_root)

        documents = RepositoryLoader(str(repo_root)).load()
        if not documents:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "No supported source files were found in the archive.",
            )

        chunks = CodeSplitter().split(documents, repository_id=repository_id)

        store = get_vector_store()
        removed = store.delete_repository(repository_id)
        store.add_documents(chunks)
    finally:
        tmp_zip.unlink(missing_ok=True)

    logger.info(
        "Indexed repository %r: %d files -> %d chunks (replaced %d)",
        repository_id, len(documents), len(chunks), removed,
    )
    return UploadResponse(
        repository_id=repository_id,
        files_loaded=len(documents),
        chunks_indexed=len(chunks),
        message=f"Indexed {len(documents)} files into {len(chunks)} chunks.",
    )
