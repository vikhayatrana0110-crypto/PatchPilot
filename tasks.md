# Tasks & Implementation Roadmap

Currently at **Step 5**. Steps 1–4 are complete.

- [x] Step 1: Environment Setup & Project Directory Structure
    - [x] Create `requirements.txt`
    - [ ] Create `.env.example` and instructions for `.env` — **regressed**: `.env` exists and works, but `.env.example` is missing from the repo root
    - [x] Verify environment installation
- [x] Step 2: Codebase Ingestion & RAG
    - [x] Implement `repository_loader.py`
    - [x] Implement `code_splitter.py`
    - [x] Implement `vector_store.py`
- [x] Step 3: Debugging Tools
    - [x] Implement `search_codebase` tool
    - [x] Implement `analyze_stack_trace` tool
    - [x] Implement `inspect_dependencies` tool
    - [x] Implement `run_syntax_check` and `run_linter` tools
    - [x] Implement `run_unit_tests` tool
    - [ ] Smoke-test each tool individually (still untested in isolation)
- [x] Step 4: LangGraph Workflow & State Machine
    - [x] Define state schemas (`state.py`)
    - [x] Implement nodes (`nodes.py` & `routing.py`) — 7 nodes plus two conditional-edge functions
    - [x] Assemble workflow graph (`workflow.py`) — conditional edges out of `generate_patch` (skip validation when no patch) and `validate_patch` (retry on syntax failure, max 3), compiled with a `MemorySaver` checkpointer
    - [x] Human-in-the-loop pause via `interrupt()` in `human_approval`
    - [x] Manual smoke test under `if __name__ == "__main__"` in `workflow.py`
- [ ] Step 5: FastAPI Backend Services
    - [ ] Implement backend routers & main app entry point
    - [ ] Connect backend to the LangGraph workflow
    - [ ] Handle human-approval resume across the `interrupt()` boundary (`Command(resume=...)` + `thread_id`)
- [ ] Step 6: Streamlit UI
    - [ ] Create main interface with repository uploading and interactive chat
    - [ ] Add patch preview, diff viewer, and human-in-the-loop validation triggers
- [ ] Step 7: Testing & Walkthrough
    - [ ] Test the platform with a sample buggy Python project
    - [ ] Document results in `walkthrough.md`
