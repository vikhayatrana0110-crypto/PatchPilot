import logging
import os
import sqlite3

# Importing anything under `backend` runs backend/__init__.py first, which loads
# the .env. Modules that read environment variables at import time therefore see
# real values no matter which entry point got here.
from backend import resolve_project_path

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command
from backend.graph.routing import (
    route_after_approval,
    route_after_patch,
    route_after_validation,
)
from backend.graph.nodes import (
    classify_error,
    finalize,
    generate_patch,
    human_approval,
    parse_issue,
    retrieve_context,
    validate_patch,
)
from backend.graph.state import DebuggingState

logger = logging.getLogger(__name__)

def create_debugging_workflow():
    """
    Creates a state graph for the debugging workflow.

    Returns:
        StateGraph: A state graph representing the debugging workflow.
    """

    logger.info("Creating debugging workflow state graph.")

    builder = StateGraph(DebuggingState)

    builder.add_node("parse_issue", parse_issue)
    builder.add_node("retrieve_context", retrieve_context)
    builder.add_node("classify_error", classify_error)
    builder.add_node("generate_patch", generate_patch)
    builder.add_node("validate_patch", validate_patch)
    builder.add_node("human_approval", human_approval)
    builder.add_node("finalize", finalize)

    builder.add_edge(START, "parse_issue")
    builder.add_edge("parse_issue", "retrieve_context")
    builder.add_edge("retrieve_context", "classify_error")
    builder.add_edge("classify_error", "generate_patch")
    builder.add_conditional_edges(
        "generate_patch",
        route_after_patch,
        {"validate_patch": "validate_patch", "finalize": "finalize"},
    )
    builder.add_conditional_edges(
        "validate_patch",
        route_after_validation,
        {"generate_patch": "generate_patch", "human_approval": "human_approval"},
    )
    builder.add_conditional_edges(
        "human_approval",
        route_after_approval,
        {"generate_patch": "generate_patch", "finalize": "finalize"},
    )
    builder.add_edge("finalize", END)


    # A file-backed checkpointer, not an in-memory one. human_approval calls
    # interrupt(), which serializes the whole session and returns; the resume
    # arrives later as a SEPARATE process/request. An in-process dict would lose
    # the session on restart and would not be visible to a second uvicorn worker.
    #
    # check_same_thread=False because uvicorn serves requests on a thread pool
    # and sqlite3 otherwise refuses connections reused across threads.
    database_url = os.getenv("DATABASE_URL", "sqlite:///./debugger.db")
    if not database_url.startswith("sqlite:///"):
        raise ValueError(
            f"DATABASE_URL must be a sqlite:/// URL for SqliteSaver, got {database_url!r}. "
            "For Postgres, swap in PostgresSaver from langgraph-checkpoint-postgres."
        )
    # Anchored to the project root: a bare './debugger.db' would otherwise be
    # created next to whatever directory uvicorn was launched from, quietly
    # starting a brand-new session store every time that differed.
    db_path = resolve_project_path(database_url.removeprefix("sqlite:///"))
    conn = sqlite3.connect(db_path, check_same_thread=False)
    checkpointer = SqliteSaver(conn)
    checkpointer.setup()

    logger.info("Checkpointer: SqliteSaver at %s", db_path)
    return builder.compile(checkpointer=checkpointer)

# Built on first use rather than at import. Importing this module used to open a
# SQLite connection and run setup() as a side effect, which meant merely
# importing it -- during test collection, or from a module that only wanted
# create_debugging_workflow -- created the database file.
_graph = None


def get_graph():
    """Return the compiled workflow, building it once on first call."""
    global _graph
    if _graph is None:
        _graph = create_debugging_workflow()
    return _graph


if __name__ == "__main__":
    #manual smoke test
    # (`python -m backend.graph.workflow`), never on import.
    initial_state = {
        "repository_id": "my_repo",
        "relative_file_path": "src/example.py",
        "issue_description": "Paste a bug description or stack trace here.",
    }


    config = {"configurable": {"thread_id": "session_123"}}

    graph = get_graph()

    result = graph.invoke(initial_state, config=config)
    print(result)

    result = graph.invoke(Command(resume={"approved": True,"feedback": "looks good"}), config=config)
    print(result)


