import logging
import os

from backend.graph.state import DebuggingState

logger = logging.getLogger(__name__)

MAX_AGENT_RETRIES = int(os.environ.get("MAX_AGENT_RETRIES", "3"))  

def route_after_patch(state: DebuggingState) -> str:
    """AFTER generate_patch: only validate when there is something to validate"""
    if not state.get("target_file") or not state.get("generated_patch"):
        logger.info("No target file or generated patch; skipping validate_patch. ie. finalize")
        return "finalize"
    return "validate_patch"

def route_after_validation(state: DebuggingState) -> str:
    """After validate_patch: retry a broken patch, otherwise hand it to the human."""
    syntax = state.get("syntax_check_result") or ""
    attempts = state.get("patch_attempts", 0)

    failed = syntax.startswith("Syntax error") or syntax.startswith("Patch application failed")

    if failed and attempts < MAX_AGENT_RETRIES:
        logger.info("routing: validation failed (attempt %d/%d) -> retry", attempts, MAX_AGENT_RETRIES)
        return "generate_patch"

    if failed:
        logger.warning("routing: still failing after %d attempts -> human_approval", attempts)

    return "human_approval"
