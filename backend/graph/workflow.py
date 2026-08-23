import logging

from dotenv import load_dotenv

# Must run BEFORE the backend imports below: several backend modules read
# environment variables at import time (nodes.MODEL_NAME,
# vector_store.EMBEDDING_MODEL, linter.REPOSITORY_STORAGE_ROOT). Loading the
# .env any later leaves those constants stuck on their fallback defaults.
load_dotenv()

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command
from backend.graph.routing import route_after_patch, route_after_validation
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
    builder.add_edge("human_approval", "finalize")
    builder.add_edge("finalize", END)


    checkpointer = MemorySaver()  # In-memory checkpointing for now; can be replaced with a persistent storage solution later.
    return builder.compile(checkpointer=checkpointer)

graph = create_debugging_workflow()

if __name__ == "__main__":
    #manual smoke test
    # (`python -m backend.graph.workflow`), never on import.
    initial_state = {
        "repository_id": "my_repo",
        "repository_path": "./storage/repositories/my_repo",
        "relative_file_path": "src/example.py",
        "issue_description": "Paste a bug description or stack trace here.",
    }


    config = {"configurable": {"thread_id": "session_123"}}
    
    result = graph.invoke(initial_state, config=config)
    print(result)

    result = graph.invoke(Command(resume={"approved": True,"feedback": "looks good"}), config=config)
    print(result)


