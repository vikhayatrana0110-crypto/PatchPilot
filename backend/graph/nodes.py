import os
import re
import logging
from pathlib import Path
from langchain_groq import ChatGroq
from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.types import interrupt

from backend.graph.state import DebuggingState
from backend.tools.stack_trace import analyze_stack_trace
from backend.tools.code_search import search_codebase
from backend.tools.dependency_inspector import inspect_dependencies
from backend.tools.linter import run_syntax_check, run_linter

logger = logging.getLogger(__name__)

GROQ_MODEL = os.environ.get("GROQ_MODEL", "openai/gpt-oss-120b")


def _get_llm() -> ChatGroq:
    return ChatGroq(model=GROQ_MODEL, temperature=0)


def _extract_section(text: str, tag: str) -> str:
    """Pull the contents of a single <tag>...</tag> block from the model's response."""
    match = re.search(rf"<{tag}>\s*(.*?)\s*</{tag}>", text, re.DOTALL)
    return match.group(1).strip() if match else text.strip()


# NODE 1 -- parse issue
def parse_issue(state: DebuggingState) -> dict:
    """
    calls analyze_stack_trace on the user's issue description.
    even if it is not on stack trace,the tool handles it gracefully.
    """
    logger.info("Node: parse_issue")
    result = analyze_stack_trace.invoke({"stack_trace": state["issue_description"]})
    return {"parsed_stack_trace": result}


# NODE 2 -- retrieve context
def retrieve_context(state: DebuggingState) -> dict:
    """
    Fetches relevant code chunks from the vector store and read dependency files.
    uses the issue description as the search query.
    """
    logger.info("Node: retrieve_context")

    context = search_codebase.invoke({
        "query": state["issue_description"],
        "repository_id": state["repository_id"],
        "k": 5
    })

    deps = inspect_dependencies.invoke({
        "repository_path": state["repository_path"]
    })

    return {
        "retrieved_context": context,
        "dependencies": deps
    }


# NODE 3 -- Classify the error
def classify_error(state: DebuggingState) -> dict:
    """
    analyzez the issue,stack trace, and retrived context
    using llm to diagnose the problem ,classify the error
    and for a debugging plan
    """
    logger.info("Node: classify_error")
    ll = _get_llm()

    system_prompt = (
        "You are a senior software engineer specializing in debugging and root-cause analysis. "
        "You will be given a bug report, an optional stack trace, relevant code context, and a "
        "dependency list. Your job is to produce a precise, evidence-based diagnosis and fix plan.\n\n"
        "Ground rules:\n"
        "- Base your analysis only on the information provided. Never invent file names, line "
        "numbers, function names, or behavior that isn't shown in the context.\n"
        "- If critical information is missing (no stack trace, no code context, etc.), say so "
        "explicitly and state what you'd need to narrow the diagnosis further. Do not guess to "
        "fill gaps.\n"
        "- Prefer the simplest explanation consistent with the evidence. If the evidence is "
        "genuinely ambiguous, list the top hypotheses ranked by likelihood instead of picking one "
        "arbitrarily.\n"
        "- Keep the fix minimal and targeted to the reported bug. Do not propose unrelated "
        "refactors, style changes, or architectural rewrites.\n"
        "- The code context may be in any programming language or framework — adapt your "
        "terminology accordingly.\n\n"
        "Respond with exactly the following structure and nothing outside it:\n\n"
        "<error_classification>\n"
        "A short label (e.g., SyntaxError, IndexError, NullReference, Logic Bug, Race Condition, "
        "Dependency Mismatch, Configuration Error, API Misuse, or a more specific category if none "
        "of these fit) followed by one sentence pinpointing the issue, e.g. 'Logic Bug: off-by-one "
        "error in the loop boundary at line 42.'\n"
        "</error_classification>\n\n"
        "<root_cause_analysis>\n"
        "Trace the causal chain from the observed symptom back to the originating code, citing "
        "specific functions, files, or lines from the provided context. State your confidence "
        "(High/Medium/Low) and briefly explain why.\n"
        "</root_cause_analysis>\n\n"
        "<debug_plan>\n"
        "Numbered, ordered steps to confirm and resolve the bug. Include a minimal code fix (a "
        "diff or before/after snippet) wherever the context supports one, and end with a "
        "verification step — a test to add or the exact repro step to re-run — to confirm the fix "
        "worked.\n"
        "</debug_plan>"
    )

    user_content = (
        f"<issue_description>\n{state['issue_description']}\n</issue_description>\n\n"
        f"<stack_trace>\n{state.get('parsed_stack_trace') or 'Not provided'}\n</stack_trace>\n\n"
        f"<code_context>\n{state.get('retrieved_context') or 'Not provided'}\n</code_context>\n\n"
        f"<dependencies>\n{state.get('dependencies') or 'Not provided'}\n</dependencies>\n\n"
        "Diagnose this issue and produce the debugging plan using the exact structure defined in "
        "your instructions."
    )

    messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=user_content)
    ]

    response = ll.invoke(messages)
    analysis_text = str(response.content)

    return {
        "error_classification": _extract_section(analysis_text, "error_classification"),
        "root_cause_analysis": _extract_section(analysis_text, "root_cause_analysis"),
        "debug_plan": _extract_section(analysis_text, "debug_plan"),
        "messages": [response],
    }


# NODE 4 -- Generate the patch
def generate_patch(state: DebuggingState) -> dict:
    """
    Takes the classified error and debug plan, and writes the actual code fix.
    Returns the target file to modify, and the exact code patch.
    """
    logger.info("Node: generate_patch")
    llm = _get_llm()
    system_prompt = (
        "You are an expert software engineer. Your task is to implement the fix for a bug "
        "based on the provided root cause analysis, debugging plan, and the current contents "
        "of the affected code.\n\n"
        "Ground Rules:\n"
        "- Base the patch only on the code shown in <code_context>. Never invent surrounding "
        "code, imports, or function signatures that aren't shown there.\n"
        "- Only output code that belongs in the final file. Do not include markdown "
        "explanations outside of the XML tags.\n"
        "- Prefer a minimal, contiguous snippet patch over rewriting the whole file. Only use "
        "a full-file replacement if the change touches most of the file or the file is very short.\n"
        "- Do not fix things that are not broken. Follow the debug plan strictly.\n\n"
        "Respond with exactly the following structure and nothing outside it:\n\n"
        "<target_file>\n"
        "The exact relative path to the single file that needs to be modified (e.g., "
        "src/main.py), and nothing else on that line.\n"
        "</target_file>\n\n"
        "<patch_type>\n"
        "Either the word 'full' (patch replaces the entire file) or 'snippet' (patch replaces "
        "only part of the file). Nothing else.\n"
        "</patch_type>\n\n"
        "<patch>\n"
        "If patch_type is 'full': the complete corrected file contents, and nothing else.\n"
        "If patch_type is 'snippet': a single block in EXACTLY this format, with no other "
        "text inside the tag:\n"
        "<<<<<<< SEARCH\n"
        "(the original lines being replaced, copied verbatim from <code_context> — same "
        "whitespace, same indentation)\n"
        "=======\n"
        "(the corrected replacement lines)\n"
        ">>>>>>> REPLACE\n"
        "The SEARCH block must match the original file's text exactly and must include enough "
        "surrounding lines to uniquely identify the location — do not use a single generic "
        "line that could match more than once.\n"
        "</patch>"
    )
    user_content = (
        f"<bug_context>\n"
        f"Issue: {state['issue_description']}\n"
        f"Root Cause: {state.get('root_cause_analysis', 'N/A')}\n"
        f"Plan: {state.get('debug_plan', 'N/A')}\n"
        f"</bug_context>\n\n"
        f"<code_context>\n{state.get('retrieved_context') or 'Not provided'}\n</code_context>\n\n"
        "Please generate the patch now."
    )
    messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=user_content)
    ]
    response = llm.invoke(messages)
    response_text = str(response.content)

    target_file = _extract_section(response_text, "target_file")
    if not target_file or "\n" in target_file or target_file.startswith("/") or ".." in target_file:
        logger.error("generate_patch: model returned an unusable target_file: %r", target_file[:200])
        target_file = None

    patch_type = _extract_section(response_text, "patch_type").strip().lower()
    if patch_type not in ("full", "snippet"):
        logger.warning("generate_patch: unexpected patch_type %r, defaulting to 'snippet'", patch_type)
        patch_type = "snippet"

    return {
        "target_file": target_file,
        "patch_type": patch_type,
        "generated_patch": _extract_section(response_text, "patch"),
        "messages": [response],
    }


_SNIPPET_PATTERN = re.compile(
    r"<<<<<<< SEARCH\s*\n(.*?)=======\s*\n(.*?)>>>>>>> REPLACE",
    re.DOTALL,
)


# Helper to apply the SEARCH/REPLACE block
def _apply_snippet(original_content: str, patch_block: str) -> str:
    """Applies a SEARCH/REPLACE block to the original file content."""
    match = _SNIPPET_PATTERN.search(patch_block)
    if not match:
        raise ValueError("Snippet patch is missing required SEARCH/REPLACE markers.")

    search_part = match.group(1).rstrip("\n")
    replace_part = match.group(2).rstrip("\n")

    if search_part not in original_content:
        raise ValueError(
            "The SEARCH block was not found in the original file — it may not match the "
            "current file content exactly."
        )

    occurrences = original_content.count(search_part)
    if occurrences > 1:
        logger.warning(
            "validate_patch: SEARCH block matches %d locations; replacing the first occurrence only.",
            occurrences,
        )

    return original_content.replace(search_part, replace_part, 1)


# NODE 5 -- Validate the patch
def validate_patch(state: DebuggingState) -> dict:
    """
    Applies the patch to the file temporarily, runs the linter and syntax tools,
    and returns the validation results.
    """
    logger.info("Node: validate_patch")

    target_file = state.get("target_file")
    patch = state.get("generated_patch")
    patch_type = state.get("patch_type", "snippet")
    repo_path = state.get("repository_path")
    repo_id = state.get("repository_id")

    if not target_file or not patch or not repo_path or not repo_id:
        return {"syntax_check_result": "Skipped: Missing file, patch, or repository info.", "lint_result": ""}

    # Defense in depth: generate_patch already screens target_file, but this is the
    # node that actually writes to disk, so re-confirm the resolved path can't escape
    # the repo root before touching anything.
    repo_root = Path(repo_path).resolve()
    full_path = (repo_root / target_file).resolve()
    if not full_path.is_relative_to(repo_root):
        logger.error("validate_patch: resolved path %s escapes repo root %s", full_path, repo_root)
        return {"syntax_check_result": "Rejected: target_file resolves outside the repository.", "lint_result": ""}

    if not full_path.is_file():
        return {"syntax_check_result": f"Skipped: {target_file} was not found in the repository.", "lint_result": ""}

    try:
        original_content = full_path.read_text(encoding="utf-8")
    except OSError as e:
        logger.error("validate_patch: could not read %s: %s", full_path, e)
        return {"syntax_check_result": f"Skipped: could not read {target_file} ({e}).", "lint_result": ""}

    # A same-directory backup on disk, not just an in-memory variable — if this
    # process gets killed outright (OOM, container eviction, tool timeout) between
    # the write below and the revert, the repo isn't left silently mutated.
    backup_path = full_path.with_name(full_path.name + ".claude_bak")

    try:
        new_content = patch if patch_type == "full" else _apply_snippet(original_content, patch)

        backup_path.write_text(original_content, encoding="utf-8")
        full_path.write_text(new_content, encoding="utf-8")

        syntax_res = run_syntax_check.invoke({"repository_id": repo_id, "file_path": target_file})
        lint_res = run_linter.invoke({"repository_id": repo_id, "file_path": target_file})

        return {"syntax_check_result": syntax_res, "lint_result": lint_res}

    except Exception as e:
        logger.error("Failed to validate patch: %s", e, exc_info=True)
        return {"syntax_check_result": f"Patch application failed: {e}", "lint_result": "N/A"}

    finally:
        # Always put the working tree back — success, failure, or anything in
        # between — since nothing has been human-approved yet.
        try:
            full_path.write_text(original_content, encoding="utf-8")
            backup_path.unlink(missing_ok=True)
        except OSError as revert_err:
            logger.critical(
                "validate_patch: FAILED TO REVERT %s — original content preserved at %s. Error: %s",
                full_path, backup_path, revert_err,
            )

# NODE 6 --Human Approval
def human_approval(state: DebuggingState) -> dict:
    """
    Pauses execution to allow a human to review the patch and the validation results
    """
    logger.info("Node: human_approval - Pausing for human intervention.")

    user_decison = interrupt({
        "action": "review_patch",
        "target_file": state.get("target_file"),
        "patch_type": state.get("patch_type"),
        "patch": state.get("generated_patch"),
        "syntax_check_result": state.get("syntax_check_result"),
        "lint_result": state.get("lint_result"),
        "plan": state.get("debug_plan"),  
    })

    approved = user_decison.get("approved", False)
    feedback = user_decison.get("feedback", "")


    return {
        "human_approved": approved,
        "human_feedback": feedback  
    }



# NODE 7 -- Finalize
def finalize(state: DebuggingState) -> dict:
    """
    if approved , applies the patch permanently. If rejected,records the failiure.
    """
    logger.info("Node: finalize")

    approved = state.get("human_approved", False)
    target_file = state.get("target_file")

    if not approved:
        report = (
            f"Debugging session aborted.\n"
            f"human rejected the path for {target_file}.\n"
            f"Feedback: {state.get('human_feedback', 'No feedback provided.')}"
        )
        return {"final_report": report}


    #Apply Permanently
    patch = state.get("generated_patch")
    patch_type = state.get("patch_type", "snippet")
    repo_path = Path(state.get("repository_path").resolve())
    full_path = (repo_path / target_file).resolve()

    try:
        orignal_content = full_path.read_text(encoding="utf-8")
        new_content = patch if patch_type == "full" else _apply_snippet(orignal_content, patch)


        full_path.write_text(new_content, encoding="utf-8")

        report = (
            f"Debugging session completed successfully.\n"
            f"Patch applied to {target_file}.\n"
            f"Syntax check: {state.get('syntax_check_result', 'N/A')}\n"
            f"Lint result: {state.get('lint_result', 'N/A')}\n"
        )

    except Exception as e:
        logger.error("Finalize: failed to apply patch permanently: %s", e, exc_info=True)
        report = f"Patch was approved but failed to apply to disk: {e}"


    return {"final_report": report}
            
        

        