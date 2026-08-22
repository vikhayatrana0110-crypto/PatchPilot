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

## Upcoming Work
- Step 4: LangGraph Workflow & State Machine
- Step 5: FastAPI Backend
- Step 6: Streamlit UI
- Step 7: End-to-end testing