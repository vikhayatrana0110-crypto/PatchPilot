import logging
import os
import re
from pathlib import Path

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_groq import ChatGroq
from langgraph.types import interrupt

from backend.graph.state import DebuggingState
from backend.tools.code_search import search_codebase
from backend.tools.dependency_inspector import inspect_dependencies
from backend.tools.linter import run_linter, run_syntax_check, resolve_repository_root, UnsafePathError
from backend.tools.stack_trace import analyze_stack_trace
from backend.tools.test_runner import run_unit_tests

logger = logging.getLogger(__name__)

MODEL_NAME = os.environ.get("MODEL_NAME", "openai/gpt-oss-120b")
MODEL_TEMPERATURE = float(os.environ.get("MODEL_TEMPERATURE", "0"))


def _get_llm() -> ChatGroq:
    return ChatGroq(model=MODEL_NAME, temperature=MODEL_TEMPERATURE)

_SECTION_TAGS = (
    "error_classification",
    "root_cause_analysis",
    "debug_plan",
    "target_file",
    "patch_type",
    "patch",
)


def _extract_section(text: str, tag: str) -> str:
    """Pull the contents of a <tag>...</tag> block, tolerating a missing closing tag.

    Returns "" when the tag is absent entirely -- callers must handle that, rather
    than silently receiving the whole response as if it were the section.
    """
    # A properly closed block always wins, so patch bodies containing markup or
    # generics (List<int>, <div>) are never truncated.
    match = re.search(rf"<{tag}>\s*(.*?)\s*</{tag}>", text, re.DOTALL)
    if match:
        return match.group(1).strip()

    # Closing tag missing -- models drop it regularly. Take everything up to the
    # next *known* section tag, or end of string.
    others = "|".join(t for t in _SECTION_TAGS if t != tag)
    match = re.search(rf"<{tag}>\s*(.*?)\s*(?=<(?:{others})>|\Z)", text, re.DOTALL)
    if not match:
        logger.warning("_extract_section: <%s> not found in model response", tag)
        return ""
    return match.group(1).strip()


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
        "repository_id": state["repository_id"]
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
    )

    # On a retry, show the model how the last attempt failed. At temperature=0 it is
    # deterministic -- without this it regenerates the identical broken patch forever.
    previous = state.get("syntax_check_result") or ""
    if previous and not previous.startswith("Syntax check passed"):
        user_content += (
            f"<previous_attempt_failed>\n{previous}\n"
            f"{state.get('lint_result') or ''}\n{state.get('test_result') or ''}\n"
            "</previous_attempt_failed>\n\n"
            "Your previous patch failed validation with the errors above. "
            "Produce a corrected patch that fixes them.\n\n"
        )

    user_content += "Please generate the patch now."

    messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=user_content)
    ]
    response = llm.invoke(messages)
    response_text = str(response.content)

    target_file = _extract_section(response_text, "target_file")
    if (
        not target_file
        or "\n" in target_file
        or target_file.startswith("/")
        or ".." in target_file
        or target_file.strip().lower() in ("n/a", "none", "unknown")
    ):
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
        "patch_attempts": state.get("patch_attempts", 0) + 1,
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


def _find_test_file(repo_root: Path, target_file:str) -> str | None:
    """
    Locate a conventional pytest file for target_file, relative to repo_root.
    run_unit_tests needs a concrete file path, and nothing in the state carries
    one -- so probe the usual layouts and give up quietly if none exist.
    """
    target = Path(target_file)
    stem = target.stem
    parent = target.parent.as_posix()

    candidates = [f"tests/test_{stem}.py", f"test_{stem}.py" ,f"tests/{stem}_test.py"]
    if parent and parent != ".":
        candidates.insert (0, f"{parent}/test_{stem}.py")
    for rel in candidates:
        if (repo_root / rel).is_file():
            return rel
    return None

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
    repo_id = state.get("repository_id")

    if not target_file or not patch or not repo_id:
        return {"syntax_check_result": "Skipped: Missing file, patch, or repository info.", "lint_result": "", "test_result": ""}

    # Derived from the id, never carried in state -- this is the same call the
    # syntax/lint/test tools make internally, so the file this node writes is
    # guaranteed to be the file they read back.
    try:
        repo_root = resolve_repository_root(repo_id)
    except UnsafePathError as e:
        return {"syntax_check_result": f"Rejected: {e}", "lint_result": "", "test_result": ""}

    # Defense in depth: generate_patch already screens target_file, but this is the
    # node that actually writes to disk, so re-confirm the resolved path can't escape
    # the repo root before touching anything.
    full_path = (repo_root / target_file).resolve()
    if not full_path.is_relative_to(repo_root):
        logger.error("validate_patch: resolved path %s escapes repo root %s", full_path, repo_root)
        return {"syntax_check_result": "Rejected: target_file resolves outside the repository.", "lint_result": "", "test_result": ""}

    if not full_path.is_file():
        return {"syntax_check_result": f"Skipped: {target_file} was not found in the repository.", "lint_result": "", "test_result": ""}

    try:
        original_content = full_path.read_text(encoding="utf-8")
    except OSError as e:
        logger.error("validate_patch: could not read %s: %s", full_path, e)
        return {"syntax_check_result": f"Skipped: could not read {target_file} ({e}).", "lint_result": "", "test_result": ""}

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

        # Must run while the patch is still on disk -- the finally block below
        # reverts the file, so anything after that would test the ORIGINAL code.

        test_file = _find_test_file(repo_root, target_file)
        if test_file:
            test_res = run_unit_tests.invoke({"repository_id": repo_id, "test_file_path": test_file})
        else:
            test_res = f"Skipped: no test file found for {target_file}."

        return {
            "syntax_check_result": syntax_res,
            "lint_result": lint_res,
            "test_result": test_res,
            }

    except Exception as e:
        logger.error("Failed to validate patch: %s", e, exc_info=True)
        return {"syntax_check_result": f"Patch application failed: {e}", "lint_result": "N/A", "test_result": ""}

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
        "test_result": state.get("test_result"),
        "plan": state.get("debug_plan"),  
    })

    # The resume payload arrives from an HTTP client once this runs behind the
    # API, so it is untrusted input. Anything that is not a mapping used to raise
    # AttributeError from inside the node, part-way through a resume, leaving the
    # session wedged. Fail closed instead: no confirmed approval means no patch.
    if not isinstance(user_decison, dict):
        logger.warning(
            "human_approval: expected a dict resume payload, got %s -- treating as rejection.",
            type(user_decison).__name__,
        )
        user_decison = {
            "approved": False,
            "feedback": (
                f"Malformed approval payload of type {type(user_decison).__name__}; "
                "expected an object with an 'approved' field."
            ),
        }

    approved = bool(user_decison.get("approved", False))
    feedback = str(user_decison.get("feedback") or "")

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

    target_file = state.get("target_file")
    patch = state.get("generated_patch")
    patch_type = state.get("patch_type", "snippet")
    repo_id = state.get("repository_id")

    # Checked BEFORE approval on purpose: when routing skips human_approval
    # (nothing was generated to review), human_approved is simply absent -- and
    # an absent key is indistinguishable from an explicit rejection, so checking
    # approval first would report "human rejected" for a patch no human ever saw.
    if not target_file or not patch or not repo_id:
        return {"final_report": "Nothing to apply: no patch was generated for this issue."}

    if not state.get("human_approved", False):
        report = (
            f"Debugging session aborted.\n"
            f"human rejected the patch for {target_file}.\n"
            f"Feedback: {state.get('human_feedback', 'No feedback provided.')}"
        )
        return {"final_report": report}

    #Apply Permanently
    try:
        repo_path = resolve_repository_root(repo_id)
    except UnsafePathError as e:
        return {"final_report": f"Rejected: {e}"}

    full_path = (repo_path / target_file).resolve()

    
    # Same defense-in-depth check validate_patch does at line 269 -- this write is
    # permanent, so it needs the containment guard more than the temporary one does.
    if not full_path.is_relative_to(repo_path):
        logger.error("finalize: resolved path %s escapes repo root %s", full_path, repo_path)
        return {"final_report": "Rejected: target_file resolves outside the repository."}

    if not full_path.is_file():
        return {"final_report": f"Skipped: {target_file} was not found in the repository."}
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
            
        

        