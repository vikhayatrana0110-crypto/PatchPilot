# Current State of the Codebase Debugging Platform (August 2026)

This document captures the exact current state of the project as of August 2026.

## Completed (Steps 1-4)

### Step 1 - Environment Setup
- `requirements.txt` created with: `langchain`, `langchain-core`, `langchain-community`, `langchain-groq`, `langchain-text-splitters`, `langchain-huggingface`, `langchain-chroma`, `langgraph`, `chromadb`, `faiss-cpu`, `sentence-transformers`, `fastapi`, `uvicorn`, `python-dotenv`, `streamlit`, `pydantic`, `charset-normalizer`, `ruff`
- `.env.example` created with `GROQ_API_KEY`, `LANGSMITH_API_KEY`, `LANGSMITH_TRACING=true`, `LANGSMITH_PROJECT`
- Virtual environment (`.venv`) created and verified

### Step 2 - RAG Pipeline
- `repository_loader.py`: **VERIFIED working**. Loads files with encoding detection, 10MB guard, path validation. Tested: loads 3-4 documents from `backend/rag/` directory.
- `code_splitter.py`: **VERIFIED working**. Language-aware chunking. Tested: 4 documents -> 14 chunks with correct line ranges and metadata.
- `vector_store.py`: **VERIFIED working**. Full integration test passed: load -> split -> store -> search. Query 'How do we split documents?' correctly retrieved `code_splitter.py` chunks. Collection cleanup (`clear()`) verified.

### Step 3 - Debugging Tools
- `code_search.py`: Implemented with lazy singleton pattern for vector store initialization.
- `stack_trace.py`: Implemented with regex-based Python traceback parser.
- `dependency_inspector.py`: Implemented with charset-normalizer encoding detection.
- `linter.py`: Implemented with full security sandbox (`_resolve_safe_path`, `SAFE_REPOSITORY_ID` regex, `UnsafePathError`).
- `test_runner.py`: Implemented, imports sandbox logic from `linter.py` (no duplication).
- **NOTE**: Tools in Step 3 have NOT been individually smoke-tested yet.

### Step 4 - LangGraph Workflow & State Machine
- `state.py`: `DebuggingState` TypedDict covering session inputs (`repository_id`, `repository_path`, `relative_file_path`, `issue_description`), tool outputs, LLM output (including `patch_type`), validation results, human approval fields, the final report, and an `add_messages`-annotated message history.
- `nodes.py`: 7 nodes implemented — `parse_issue`, `retrieve_context`, `classify_error`, `generate_patch`, `validate_patch`, `human_approval`, `finalize`. LLM sections are parsed from deterministic XML `<tags>` via `_extract_section`, which now tolerates a missing closing tag and returns `""` (rather than the whole response) when a tag is absent.
- `routing.py`: two conditional-edge functions. `route_after_patch` skips validation and human approval when no patch was generated (routes straight to `finalize`). `route_after_validation` routes a syntax/apply failure back to `generate_patch` up to `MAX_AGENT_RETRIES` (3), otherwise forward to `human_approval`.
- `workflow.py`: `StateGraph` — `START -> parse_issue -> retrieve_context -> classify_error -> generate_patch`, then conditional edges out of `generate_patch` and `validate_patch`, converging on `human_approval -> finalize -> END`. Compiled with a `MemorySaver` checkpointer.
- Human-in-the-loop: `human_approval` calls `interrupt()`; the session resumes with `Command(resume={"approved": ..., "feedback": ...})` against the same `thread_id`.
- `load_dotenv()` runs at the top of `workflow.py`, before backend imports, because `nodes.MODEL_NAME`, `vector_store.EMBEDDING_MODEL`, and `linter.REPOSITORY_STORAGE_ROOT` are read at import time.
- Model config is env-driven: `MODEL_NAME` (default `openai/gpt-oss-120b`) and `MODEL_TEMPERATURE` (default `0`).
- **NOTE**: exercised only via the manual smoke test in `workflow.py` `__main__`; no automated tests yet.

## Not Started (Steps 5-7)
- **Step 5**: FastAPI Backend Services (`main.py`, API routes)
- **Step 6**: Streamlit UI (`app.py`)
- **Step 7**: Testing & Walkthrough

## Known Issues / Bugs Fixed During Development
1. `repository_loader.py`: `documents.append(doc)` was outside the for loop — only appended last file. Fixed by indenting into the loop.
2. `code_splitter.py`: Initially produced 0 chunks because only `__init__.py` (empty file) was being loaded. Fixed by loading the correct directory.
3. `vector_store.py`: Original design had optional `filter_dict` — allowed cross-repository data leakage. Fixed by making `repository_id` mandatory in `search()` and `get_retriever()`.
4. `linter.py`: `logger.info` had 3 args but only 1 `%s` placeholder. Fixed by adding second `%s`.
5. `finalize`: applied the patch without a containment check and called `.resolve()` on a `str`. Fixed by resolving the repo path first, re-running the `is_relative_to` guard before the permanent write, and bailing out when target file / patch / repo path are missing.
6. `_extract_section`: fell back to returning the entire model response when a tag was missing, so a dropped `<target_file>` silently became the whole answer. Now returns `""` and logs a warning.
7. `generate_patch`: accepted `"N/A"` / `"none"` / `"unknown"` as a `target_file`. Now rejected alongside absolute paths and `..`.

## Open Questions
- How the `interrupt()` / resume cycle maps onto a stateless REST API in Step 5 — the approval endpoint needs a session store keyed by `thread_id`.
- `validate_patch` mutates the real file with a `.claude_bak` backup and reverts in a `finally` — safety under concurrent sessions is unverified.
- Groq free-tier rate limits under a multi-step agent loop — untested.
- `.env.example` is missing from the repo root even though `.env` exists.
