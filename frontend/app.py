"""
Streamlit UI for the codebase debugging platform.

Runs as its own server process and talks to the FastAPI backend over HTTP,
server-to-server -- the browser never contacts the API directly, so no CORS
configuration is involved.

uvicorn backend.api.main:app --reload --reload-dir backend --port 8000     # terminal 1
streamlit run frontend/app.py                          # terminal 2

Streamlit re-executes this entire file on every interaction, so anything that
must outlive a button click lives in st.session_state. A session_id kept in a
plain variable would vanish at exactly the moment it is needed -- when the user
clicks Approve.
"""

import os

import requests
import streamlit as st

API_BASE = os.getenv("API_BASE_URL", "http://127.0.0.1:8000")

"""
Three very different timeouts. A debugging run makes LLM calls, lints, and runs pytest -- possibly three times over, since the agent retries -- so a
short timeout would kill legitimate work. But requests defaults to NO timeout,
which hangs forever against a dead server.
"""
HEALTH_TIMEOUT = 3
QUICK_TIMEOUT = 10
UPLOAD_TIMEOUT = 120
DEBUG_TIMEOUT = 240



# API client


class ApiError(Exception):
    """An API call failed in a way worth showing the user verbatim."""


def _request(method: str, path: str, timeout: int = QUICK_TIMEOUT, **kwargs) -> dict:
    """One place for the base URL, timeouts, and error translation.

    FastAPI returns {"detail": "..."} on 4xx, and those messages were written to
    be read by a human -- "Repository 'x' has not been uploaded", "This session
    has already finished and cannot be resumed". Surfacing that text beats
    showing a bare status code.
    """
    url = f"{API_BASE}{path}"
    try:
        response = requests.request(method, url, timeout=timeout, **kwargs)
    except requests.exceptions.ConnectionError:
        raise ApiError(f"Cannot reach the API at {API_BASE}. Is the backend running?")
    except requests.exceptions.Timeout:
        raise ApiError(f"The API did not respond within {timeout}s.")
    except requests.exceptions.RequestException as e:
        raise ApiError(f"Request failed: {e}")

    if response.status_code >= 400:
        try:
            detail = response.json().get("detail")
        except ValueError:
            detail = None
        if isinstance(detail, list):
            # FastAPI validation errors arrive as a list of {loc, msg, type}.
            detail = "; ".join(d.get("msg", str(d)) for d in detail)
        raise ApiError(detail or f"{response.status_code} {response.reason}")

    return response.json()


def api_health() -> bool:
    try:
        return _request("GET", "/health", timeout=HEALTH_TIMEOUT).get("status") == "ok"
    except ApiError:
        return False


def api_list_repositories() -> list[dict]:
    return _request("GET", "/repositories")


def api_upload_repository(repository_id: str, data: bytes, filename: str) -> dict:
    return _request(
        "POST",
        "/repositories/upload",
        timeout=UPLOAD_TIMEOUT,
        data={"repository_id": repository_id},
        files={"file": (filename, data, "application/zip")},
    )


def api_start_debug(repository_id: str, issue_description: str) -> dict:
    return _request(
        "POST",
        "/debug",
        timeout=DEBUG_TIMEOUT,
        json={"repository_id": repository_id, "issue_description": issue_description},
    )


def api_resume_session(session_id: str, action: str, feedback: str = "") -> dict:
    return _request(
        "POST",
        f"/sessions/{session_id}/approve",
        timeout=DEBUG_TIMEOUT,
        json={"action": action, "feedback": feedback},
    )


def api_get_session(session_id: str) -> dict:
    return _request("GET", f"/sessions/{session_id}")



# Session state


DEFAULT_STATE = {
    "status": "idle",          # idle | awaiting_approval | completed
    "session_id": None,
    "review": None,
    "final_report": None,
    "repository_id": None,
}

# Widget values are cleared by DELETING their keys, never by assigning to them:
# a widget already instantiated this run will not accept a new value, whereas a
# deleted key simply re-initialises to its default on the next run.
INPUT_KEYS = ("widget_issue", "widget_feedback")


def init_state() -> None:
    for key, value in DEFAULT_STATE.items():
        st.session_state.setdefault(key, value)


def clear_inputs() -> None:
    for key in INPUT_KEYS:
        st.session_state.pop(key, None)


def apply_response(response: dict) -> None:
    """Absorb a DebugResponse from either /debug or /sessions/{id}/approve.

    Both endpoints return the same model, which is why this is one function and
    "revise" needs no special case: it comes back as awaiting_approval carrying
    a NEW patch, the state updates, and the review pane simply redraws.
    """
    st.session_state.session_id = response["session_id"]
    st.session_state.status = response["status"]
    st.session_state.review = response.get("review")
    st.session_state.final_report = response.get("final_report")


def reset_session() -> None:
    for key, value in DEFAULT_STATE.items():
        if key != "repository_id":     # keep the picker where the user left it
            st.session_state[key] = value
    clear_inputs()


# ---------------------------------------------------------------------------
# Rendering helpers
# ---------------------------------------------------------------------------

# These mirror the verdict prefixes produced in backend/graph/nodes.py and
# backend/tools/linter.py. This is duplicated knowledge across an HTTP boundary:
# reword a verdict there and the icons here go quietly wrong. Unknown text is
# deliberately shown as neutral rather than as a failure, so a prefix that
# drifts degrades to "no opinion" instead of a false red cross.
PASS_PREFIXES = (
    "Syntax check passed",
    "Linting Passed",
    "Tests now pass",
    "Tests passed",
)
FAIL_PREFIXES = (
    "Syntax error",
    "Linting Issues",
    "Tests STILL FAILING",
    "Tests BROKEN BY PATCH",
    "Target file not found",
    "Patch application failed",
    "Rejected:",
)


def verdict_icon(result: str | None) -> str:
    text = (result or "").strip()
    if not text or text.startswith("Skipped"):
        return "⬜"
    if text.startswith(PASS_PREFIXES):
        return "✅"
    if text.startswith(FAIL_PREFIXES):
        return "❌"
    return "⬜"


def render_validation(label: str, result: str | None) -> None:
    """Verdict line up front, full output tucked into an expander.

    test_result is a one-line verdict followed by an entire pytest dump. The
    verdict is what a reviewer decides on; the dump is what they read only when
    the verdict surprises them.
    """
    text = (result or "Not run").strip()
    first_line, _, rest = text.partition("\n")
    st.markdown(f"{verdict_icon(result)} **{label}** — {first_line}")
    if rest.strip():
        with st.expander(f"Full {label.lower()} output"):
            st.code(rest.strip(), language="text")


# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------

def render_sidebar() -> bool:
    """Draw the sidebar. Returns whether the API is reachable."""
    st.sidebar.title("⚙️ Setup")

    # Without this, a stopped backend just looks like a broken UI.
    if not api_health():
        st.sidebar.error(f"API unreachable at {API_BASE}")
        st.sidebar.code("uvicorn backend.api.main:app --reload --reload-dir backend --port 8000", language="bash")
        return False

    st.sidebar.success(f"API healthy — {API_BASE}")

    st.sidebar.subheader("Repository")
    try:
        repositories = api_list_repositories()
    except ApiError as e:
        st.sidebar.error(str(e))
        repositories = []

    if repositories:
        labels = {
            r["repository_id"]: f"{r['repository_id']}  ({r['chunks_indexed']} chunks)"
            for r in repositories
        }
        ids = list(labels)
        current = st.session_state.repository_id
        # No key= on purpose. A keyed widget restores its own stored value and
        # ignores index=, so selecting a freshly uploaded repository from code
        # would silently fail.
        chosen = st.sidebar.selectbox(
            "Choose a repository",
            ids,
            index=ids.index(current) if current in ids else 0,
            format_func=lambda rid: labels[rid],
        )
        st.session_state.repository_id = chosen

        if any(r["repository_id"] == chosen and r["chunks_indexed"] == 0 for r in repositories):
            st.sidebar.warning("This repository has no indexed chunks. Re-upload it.")
    else:
        st.sidebar.info("No repositories yet. Upload one below.")

    st.sidebar.divider()
    st.sidebar.subheader("Upload a repository")
    upload = st.sidebar.file_uploader("Zip archive", type=["zip"], key="widget_zip")
    new_id = st.sidebar.text_input(
        "Repository id",
        placeholder="my_repo",
        help="Letters, digits, underscores and hyphens only.",
        key="widget_new_repo_id",
    )
    if st.sidebar.button("Upload and index", use_container_width=True):
        if not upload or not new_id.strip():
            st.sidebar.error("Pick a .zip file and enter an id.")
        else:
            try:
                with st.spinner("Extracting and indexing…"):
                    # getvalue(), not read(): Streamlit re-runs this script
                    # constantly and read() would consume the buffer, so a
                    # later run would upload zero bytes.
                    result = api_upload_repository(
                        new_id.strip(), upload.getvalue(), upload.name
                    )
                st.session_state.repository_id = result["repository_id"]
                st.sidebar.success(result["message"])
                st.rerun()
            except ApiError as e:
                st.sidebar.error(str(e))

    st.sidebar.divider()
    st.sidebar.subheader("Reconnect to a session")
    existing = st.sidebar.text_input("Session id", key="widget_session_lookup")
    if st.sidebar.button("Load session", use_container_width=True):
        if not existing.strip():
            st.sidebar.error("Enter a session id.")
        else:
            try:
                apply_response(api_get_session(existing.strip()))
                clear_inputs()
                st.rerun()
            except ApiError as e:
                st.sidebar.error(str(e))

    return True


# ---------------------------------------------------------------------------
# Main panes
# ---------------------------------------------------------------------------

def render_idle() -> None:
    st.subheader("Describe the problem")

    repository_id = st.session_state.repository_id
    if not repository_id:
        st.info("Choose or upload a repository in the sidebar to begin.")
        return

    issue = st.text_area(
        "Bug report, error message, or stack trace",
        height=180,
        placeholder="test_add fails: add(2, 3) returns -1 instead of 5.",
        key="widget_issue",
    )

    if st.button("Start debugging", type="primary"):
        if not issue.strip():
            st.error("Describe the issue first.")
            return
        try:
            # Tens of seconds of real work. Without a spinner the page looks
            # frozen and people click again.
            with st.spinner("Retrieving code, diagnosing, patching, validating…"):
                response = api_start_debug(repository_id, issue.strip())
            apply_response(response)
            st.rerun()
        except ApiError as e:
            st.error(str(e))


def render_review() -> None:
    review = st.session_state.review or {}

    st.subheader("Proposed patch")
    st.caption("Session id — keep this to reconnect later:")
    st.code(st.session_state.session_id or "unknown", language="text")

    left, right = st.columns([3, 2])
    left.markdown(f"**File:** `{review.get('target_file') or 'unknown'}`")
    patch_type = review.get("patch_type") or "snippet"
    right.markdown(
        f"**Patch type:** `{patch_type}` — "
        + ("replaces part of the file" if patch_type == "snippet" else "replaces the whole file")
    )

    st.markdown("#### Validation")
    render_validation("Syntax", review.get("syntax_check_result"))
    render_validation("Lint", review.get("lint_result"))
    render_validation("Tests", review.get("test_result"))

    st.markdown("#### Patch")
    # A snippet is a SEARCH/REPLACE block, not source -- Python highlighting
    # would mangle the <<<<<<< markers. Only a full patch is real code.
    st.code(
        review.get("patch") or "(empty)",
        language="python" if patch_type == "full" else "text",
    )

    if review.get("plan"):
        with st.expander("The model's debugging plan"):
            st.markdown(review["plan"])

    st.divider()
    st.markdown("#### Your decision")
    feedback = st.text_area(
        "Feedback — required to request changes, recorded on a rejection",
        placeholder="Right idea, but you fixed the wrong function.",
        key="widget_feedback",
    )

    approve_col, revise_col, reject_col = st.columns(3)

    if approve_col.button("✅ Approve", type="primary", use_container_width=True):
        _submit("approve", feedback)

    if revise_col.button("✏️ Request changes", use_container_width=True):
        if not feedback.strip():
            st.error("Requesting changes needs feedback for the model to act on.")
        else:
            _submit("revise", feedback)

    if reject_col.button("🚫 Reject", use_container_width=True):
        _submit("reject", feedback)


def _submit(action: str, feedback: str) -> None:
    """Send a decision and absorb whatever comes back.

    Makes no assumption about where the session lands. A "revise" returns
    another review rather than a final report, so apply_response reads `status`
    and the next render picks the right pane.
    """
    spinner = "Regenerating the patch…" if action == "revise" else "Finishing up…"
    try:
        with st.spinner(spinner):
            response = api_resume_session(
                st.session_state.session_id, action, feedback.strip()
            )
        apply_response(response)
        # Cleared so the next revision starts blank -- otherwise stale feedback
        # sits in the box and can be resubmitted against a patch that already
        # addressed it.
        clear_inputs()
        st.rerun()
    except ApiError as e:
        st.error(str(e))


def render_completed() -> None:
    st.subheader("Session complete")
    report = st.session_state.final_report or "No report was produced."

    if report.startswith("Debugging session completed successfully"):
        st.success("Patch applied.")
    else:
        st.warning("No patch was applied.")

    st.code(report, language="text")
    st.caption("Session id:")
    st.code(st.session_state.session_id or "unknown", language="text")

    if st.button("Start another", type="primary"):
        reset_session()
        st.rerun()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    st.set_page_config(page_title="Codebase Debugging Platform", page_icon="🔧", layout="wide")
    init_state()

    st.title("🔧 Codebase Debugging Platform")
    st.caption("Upload a repository, describe a bug, and review the patch the agent proposes.")

    if not render_sidebar():
        st.error("The backend is not reachable, so nothing here will work yet.")
        st.stop()

    status = st.session_state.status
    if status == "awaiting_approval":
        render_review()
    elif status == "completed":
        render_completed()
    elif status == "idle":
        render_idle()
    else:
        # Defensive: an unrecognised status means the API and this UI disagree.
        st.error(f"Unrecognised session status {status!r}. Starting over.")
        reset_session()


main()