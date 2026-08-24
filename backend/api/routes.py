"""HTTP routes for the debugging platform."""
import uuid
import logging
import os
import shutil
import stat
import tempfile
import zipfile
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, UploadFile, status


from langgraph.types import Command


from backend.rag.code_splitter import CodeSplitter
from backend.rag.repository_loader import RepositoryLoader
from backend.rag.vector_store import get_vector_store
from backend.tools.linter import (
    SAFE_REPOSITORY_ID,
    UnsafePathError,
    resolve_repository_root,
    REPOSITORY_STORAGE_ROOT,
)

from backend.api.schemas import (
    ApprovalRequest,
    DebugRequest,   
    DebugResponse,
    ReviewPayload,
    SessionStatus,
    UploadResponse,
    RepositorySummary,
)
from backend.graph.workflow import get_graph



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




def _session_config(session_id:str) -> dict:
    """
    the graph addresses a session by thread_id;
    The API calls it session_id
    """

    return {"configurable":{"thread_id":session_id}}


def _to_response(session_id: str, result: dict) -> DebugResponse:
    """
    Translate a graph result into the API's single response shape.
    A run ends in exactly one of two places: paused at the approval interrupt,
    or finished. Both /debug and /approve funnel through here, so a client only
    ever branches on `status` -- never on which endpoint it called.
    """

    interrupts = result.get("__interrupt__")
    if interrupts:
        # human_approval passes a dict to interrupt(). Its extra "action"
        # marker key is ignored -- Pydantic drops unknown fields by default.
        return DebugResponse(
            session_id=session_id,
            status=SessionStatus.AWAITING_APPROVAL,
            review=ReviewPayload(**interrupts[0].value),
        )

    return DebugResponse(
        session_id=session_id,
        status=SessionStatus.COMPLETED,
        final_report=result.get("final_report") or "Session finished without a report.",
    )


@router.post(
    "/debug",
    response_model=DebugResponse,
    summary="Start a debugging session",
)

def start_debugging(request: DebugRequest) -> DebugResponse:
    """
    Run the agent until it has a patch to review, or finishes without one.
    Deliberately a plain `def`, not `async def`. graph.invoke() blocks for tens
    of seconds -- Groq calls, a linter, a pytest run -- and on the event loop
    that would stall every other request, /health included. FastAPI runs a
    non-async endpoint in a threadpool instead.
    """
    try:
        repo_root = resolve_repository_root(request.repository_id)
    except UnsafePathError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT,str(e))

    # Checked up front: without it the graph runs a full retrieval and two LLM
    # calls against an empty repository before failing to find anything.

    if not repo_root.is_dir():
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            f"Repository {request.repository_id!r} has not been uploaded. "
            "Send it to /repositories/upload first.",
        )

    session_id = uuid.uuid4().hex
    logger.info("Starting session %s for repository %r",session_id,request.repository_id)

    try:
        result = get_graph().invoke(
            {
                "repository_id": request.repository_id,
                "issue_description": request.issue_description,
            },
            config=_session_config(session_id),
        )
    except Exception as e:
        logger.error("Session %s failed: %s", session_id, e, exc_info=True)
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            f"the debugging run failed: {e}",
        )

    return _to_response(session_id, result)



@router.post(
    "/sessions/{session_id}/approve",
    response_model=DebugResponse,
    summary="Approve, reject, or request changes to a proposed patch",
)
def resume_session(session_id: str, request: ApprovalRequest) -> DebugResponse:
    """Resume a session paused at the approval interrupt.

    Returns a COMPLETED response for approve and reject. For "revise" it
    returns another AWAITING_APPROVAL payload instead -- the model produces a
    new patch and the session pauses again -- which is exactly why both
    endpoints share one response model.
    """
    config = _session_config(session_id)
    snapshot = get_graph().get_state(config)

    # created_at is None only when nothing was ever checkpointed under this id.
    # A finished session HAS a created_at but an empty `next`, so the two cases
    # need separate checks and deserve different status codes.
    if snapshot.created_at is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, f"No session with id {session_id!r}."
        )

    if not snapshot.next:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "This session has already finished and cannot be resumed.",
        )

    logger.info("Resuming session %s with action=%s", session_id, request.action)

    try:
        result = get_graph().invoke(
            Command(resume={"action": request.action, "feedback": request.feedback}),
            config=config,
        )
    except Exception as e:
        logger.error("Session %s failed on resume: %s", session_id, e, exc_info=True)
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, f"Resuming the session failed: {e}"
        )

    return _to_response(session_id, result)


@router.get(
    "/sessions/{session_id}",
    response_model=DebugResponse,
    summary="Look up the current state of a session",
)
def get_session(session_id: str) -> DebugResponse:
    """Report where a session stands without advancing it.

    Reads the review from snapshot.interrupts rather than snapshot.values on
    purpose. The state dict names the fields `generated_patch` and `debug_plan`,
    while ReviewPayload calls them `patch` and `plan` -- so building it from
    .values would not error, it would silently return a review with no patch and
    no plan in it. The interrupt payload is the exact dict human_approval sent.
    """
    snapshot = get_graph().get_state(_session_config(session_id))

    if snapshot.created_at is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, f"No session with id {session_id!r}."
        )

    if snapshot.next and snapshot.interrupts:
        return DebugResponse(
            session_id=session_id,
            status=SessionStatus.AWAITING_APPROVAL,
            review=ReviewPayload(**snapshot.interrupts[0].value),
        )

    return DebugResponse(
        session_id=session_id,
        status=SessionStatus.COMPLETED,
        final_report=snapshot.values.get("final_report")
        or "Session finished without a report.",
    )


@router.get(
    "/repositories",
    response_model=list[RepositorySummary],
    summary="List uploaded repositories",
)

def list_repositories() -> list[RepositorySummary]:
    """
    Every repository on disk, with how much of it is indexed.

    Directory and index can disagree -- a repository extracted before an
    indexing failure has files but no chunks -- so the count is reported rather
    than assumed, and a zero tells the caller to re-upload.
    """
    collection = get_vector_store().vectorstore._collection

    summaries: list[RepositorySummary] = []
    for path in sorted(REPOSITORY_STORAGE_ROOT.iterdir()):
        if not path.is_dir() or not SAFE_REPOSITORY_ID.match(path.name):
            continue
        ids = collection.get(where={"repository_id": path.name}, include=[])["ids"]
        summaries.append(
            RepositorySummary(repository_id=path.name, chunks_indexed=len(ids))
        )
    return summaries
