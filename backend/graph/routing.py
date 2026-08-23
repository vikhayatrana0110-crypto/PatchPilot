import logging
import os

from backend.graph.nodes import (
    TARGET_NOT_FOUND,
    TEST_BROKEN_BY_PATCH,
    TEST_STILL_FAILING,
)
from backend.graph.state import DebuggingState

logger = logging.getLogger(__name__)

MAX_AGENT_RETRIES = int(os.environ.get("MAX_AGENT_RETRIES", "3"))

def route_after_patch(state: DebuggingState) -> str:
    """AFTER generate_patch: only validate when there is something to validate"""
    if not state.get("target_file") or not state.get("generated_patch"):
        logger.info("No target file or generated patch; skipping validate_patch. ie. finalize")
        return "finalize"
    return "validate_patch"


def _validation_failure(state: DebuggingState) -> str | None:
    """Name the recoverable failure in the last validation, or None if there isn't one.

    Recoverable means another generate_patch attempt could plausibly fix it. A
    path-traversal rejection is not recoverable and deliberately isn't listed --
    retrying would just produce the same rejection three more times.
    """
    syntax = state.get("syntax_check_result") or ""
    tests = state.get("test_result") or ""

    if syntax.startswith("Syntax error"):
        return "patch has a syntax error"
    if syntax.startswith("Patch application failed"):
        return "patch could not be applied"
    if syntax.startswith(TARGET_NOT_FOUND):
        return "target file does not exist"
    if tests.startswith(TEST_BROKEN_BY_PATCH):
        return "patch broke previously passing tests"
    if tests.startswith(TEST_STILL_FAILING):
        return "tests still fail, so the bug is not fixed"
    return None


def route_after_validation(state: DebuggingState) -> str:
    """After validate_patch: retry a broken patch, otherwise hand it to the human."""
    failure = _validation_failure(state)
    attempts = state.get("patch_attempts", 0)

    if failure is None:
        return "human_approval"

    if attempts < MAX_AGENT_RETRIES:
        logger.info(
            "routing: %s (attempt %d/%d) -> retry", failure, attempts, MAX_AGENT_RETRIES
        )
        return "generate_patch"

    logger.warning(
        "routing: %s, still failing after %d attempts -> human_approval", failure, attempts
    )
    return "human_approval"


def route_after_approval(state: DebuggingState) -> str:
    """After human_approval: a revision request loops back, everything else ends.

    Deliberately not capped by MAX_AGENT_RETRIES -- that budget exists to stop an
    unattended agent spinning on its own broken output. A human asking for
    another revision is a person making a choice each time, so it is theirs to
    make. The automatic retry budget may already be spent by then, which only
    means later failures reach them sooner.
    """
    if state.get("human_decision") == "revise":
        logger.info("routing: human requested a revision -> generate_patch")
        return "generate_patch"
    return "finalize"
