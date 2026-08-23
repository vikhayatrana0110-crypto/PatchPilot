# Agentic Codebase Debugging Platform - Changelog

## Session 1 (Late July 2026)
- Created initial implementation plan with 6-step roadmap
- Resolved open questions: Groq free tier (no paid APIs), ChromaDB (not FAISS), local HuggingFace embeddings, Python 3.14.6, LangSmith connected
- Created `requirements.txt` with all dependencies
- Created `.env.example` for environment configuration
- Set up virtual environment and installed dependencies
- **Fixed**: had to install `langchain-text-splitters` and `charset-normalizer` separately

## Session 2 (Early August 2026)
- Implemented `repository_loader.py` with `charset-normalizer` encoding detection, 10MB file size guard, and directory pruning
- **Bug fix**: `documents.append(doc)` was outside the for loop — only ever appended the last file processed
- **Verified**: RepositoryLoader loads 3 documents from `backend/rag/`
- Implemented `code_splitter.py` with language-aware RecursiveCharacterTextSplitter
- **Bug fix**: 0 chunks produced initially because only empty `__init__.py` was being loaded
- **Verified**: 4 documents split into 14 chunks with correct line ranges
- Implemented `vector_store.py` with ChromaDB + HuggingFace embeddings
- **Security fix**: Made `repository_id` mandatory in `search()` and `get_retriever()` to prevent cross-repository data leakage
- Added missing_repo_id warning in `add_documents()`
- Full integration test passed: load -> split -> store -> search -> clear
- Implemented `code_search.py` with lazy singleton pattern
- Implemented `stack_trace.py` with regex-based traceback parser
- Implemented `dependency_inspector.py` with `charset-normalizer`
- Implemented `linter.py` with full security sandbox (`SAFE_REPOSITORY_ID` regex, `_resolve_safe_path`, `UnsafePathError`, `pathlib.relative_to`)
- **Bug fix**: logger.info had mismatched `%s` count
- Implemented `test_runner.py` importing sandbox logic from `linter.py`
- Renamed `Safe_repository_ID` to `SAFE_REPOSITORY_ID` (constant naming convention)

## Session 3 (Late August 2026) - Step 4 complete
- Implemented `state.py`: `DebuggingState` TypedDict; added `repository_path` (absolute repo root on disk) and `patch_type` (`full` | `snippet`)
- Implemented `nodes.py` with 7 nodes: `parse_issue`, `retrieve_context`, `classify_error`, `generate_patch`, `validate_patch`, `human_approval`, `finalize`
- `classify_error` produces root cause analysis *and* debug plan in one Groq call using deterministic XML `<tags>` - the separately planned `plan_debugging` node was dropped
- `validate_patch` mutates the target file behind a `.claude_bak` backup, runs the sandboxed syntax check + Ruff, and reverts in a `finally` block
- `human_approval` uses LangGraph `interrupt()`; resume via `Command(resume={"approved": ..., "feedback": ...})` on the same `thread_id`
- Implemented `workflow.py`: `StateGraph` compiled with a `MemorySaver` checkpointer, plus a manual smoke test under `__main__`
- Model config moved to env vars: `GROQ_MODEL` renamed to `MODEL_NAME`, added `MODEL_TEMPERATURE`
- `load_dotenv()` moved above the backend imports in `workflow.py` - `nodes.MODEL_NAME`, `vector_store.EMBEDDING_MODEL`, and `linter.REPOSITORY_STORAGE_ROOT` are read at import time and were sticking to their fallback defaults
- **Bug fix**: `_extract_section` returned the entire model response when a tag was absent; now returns `""`, logs a warning, and tolerates a missing closing tag by scanning to the next known section tag
- **Bug fix**: `finalize` called `.resolve()` on a `str` and wrote without a containment check; now resolves the repo path first, re-runs the `is_relative_to` guard, and bails out on missing target file / patch / repo path
- **Bug fix**: `generate_patch` accepted `"N/A"` / `"none"` / `"unknown"` as a valid `target_file`

## Session 4 (23 August 2026) - Steps 3 and 4 closed
- **`run_unit_tests` was orphaned** — built in Step 3 but never called by the graph. Now invoked from `validate_patch` via a new `_find_test_file` helper that probes conventional paths (`tests/test_<stem>.py`, `test_<stem>.py`). The call sits inside the `try` block so tests run against the *patched* file, before the `finally` reverts it.
- Added `backend/graph/routing.py` with two conditional-edge functions:
  - `route_after_patch`: skips `validate_patch` and `human_approval` entirely when no `target_file`/`generated_patch` exists, routing to `finalize`
  - `route_after_validation`: routes back to `generate_patch` on syntax/apply failure up to `MAX_AGENT_RETRIES` (3), otherwise forward to `human_approval`
- `workflow.py` now uses `add_conditional_edges` for those two branches instead of plain edges
- `state.py`: added `test_result` (unit test output) and `patch_attempts` (retry counter, incremented in `generate_patch`)
- `generate_patch` now feeds the prior attempt's syntax/lint/test failures back into the prompt inside a `<previous_attempt_failed>` block — required, because at `MODEL_TEMPERATURE=0` the model otherwise regenerates the byte-identical broken patch on every retry
- **Bug fix**: `finalize` reported "human rejected the patch" when routing had *skipped* `human_approval` — with no patch to approve, `human_approved` is simply absent from state and was indistinguishable from an explicit `False`. Reordered to check the no-patch case first; it now reports "Nothing to apply".
- **Bug fix**: five early-return paths in `validate_patch` omitted `test_result`, so a stale pass/fail from a prior attempt survived into the next retry (LangGraph preserves unset keys). All five now clear it.
- Verified end-to-end against a throwaway repo (`storage/repositories/my_repo`, gitignored): a correct fix passes pytest, a syntax-breaking fix is caught, a mismatched SEARCH block is caught, and the target file reverts cleanly in every case.

## Upcoming Work
- Step 5: FastAPI Backend
- Step 6: Streamlit UI
- Step 7: End-to-end testing