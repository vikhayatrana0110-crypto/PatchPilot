# Architecture Decision Records (ADRs)

This document contains the Architecture Decision Records for the Agentic Codebase Debugging Platform.

## ADR-001: Use Groq API with LLaMA models for LLM inference
* **Status**: Accepted
* **Context**: Need an LLM for agent reasoning. No budget for paid APIs.
* **Decision**: Use Groq's free tier with llama3-70b-8192 or llama-3.1-70b-versatile.
* **Consequences**: Fast inference, free, but rate-limited. May need to handle rate limit errors gracefully.

## ADR-002: Use local HuggingFace embeddings instead of API-based
* **Status**: Accepted
* **Context**: Need embeddings for vector search. No budget for OpenAI/Cohere embedding APIs.
* **Decision**: Use sentence-transformers/all-MiniLM-L6-v2 locally via langchain-huggingface.
* **Consequences**: Free, no API calls, but requires ~90MB model download on first run. Runs on CPU.

## ADR-003: Use ChromaDB over FAISS for vector storage
* **Status**: Accepted
* **Context**: Need a vector database. Both Chroma and FAISS are local and free.
* **Decision**: ChromaDB. Better fit for this project.
* **Consequences**: Built-in persistence, metadata filtering ($and syntax), good LangChain integration. Slightly more overhead than FAISS but more feature-rich.

## ADR-004: Single Chroma collection with mandatory repository_id filter
* **Status**: Accepted
* **Context**: Multiple repositories will be uploaded. Options: (a) separate collection per repo, (b) single shared collection with metadata filtering.
* **Decision**: Single collection 'codebase_index' with mandatory repository_id in search() and get_retriever().
* **Consequences**: Simpler collection management. But requires strict discipline — forgetting the filter causes cross-repo data leakage. Mitigated by making repository_id a required argument (not optional).

## ADR-005: Language-aware code chunking with 1500/200 parameters
* **Status**: Accepted
* **Context**: Need to chunk source code for embedding. Generic text splitters break code at arbitrary points.
* **Decision**: Use RecursiveCharacterTextSplitter with Language enum. 1500 char chunks, 200 char overlap.
* **Consequences**: Preserves class/function boundaries. Overlap ensures context continuity. Good balance of retrieval quality and token efficiency.

## ADR-006: charset-normalizer for file reading
* **Status**: Accepted
* **Context**: Source files may have various encodings (UTF-8, Latin-1, Windows-1252, etc.). Assuming UTF-8 causes silent byte-mangling.
* **Decision**: Use charset-normalizer's from_path().best() for all file reading.
* **Consequences**: Correct encoding detection. Small performance overhead. Returns None for binary files (handled gracefully).

## ADR-007: Security sandbox for subprocess tools
* **Status**: Accepted
* **Context**: LLM agents can hallucinate file paths. Tools like run_linter and run_unit_tests execute subprocesses. Path traversal could access files outside the repo.
* **Decision**: Implemented multi-layer sandbox: (1) SAFE_REPOSITORY_ID regex rejects malicious repo IDs, (2) absolute paths rejected, (3) pathlib.relative_to() for symlink-safe containment, (4) .py extension check, (5) subprocess uses list form (no shell=True), (6) sys.executable for venv isolation.
* **Consequences**: Defense-in-depth. Cannot access files outside storage/repositories/<repository_id>/. Small code overhead but critical for security.

## ADR-008: Lazy singleton for vector store in code_search.py
* **Status**: Accepted
* **Context**: Original implementation loaded the HuggingFace embedding model (~90MB) at module import time.
* **Decision**: Use lazy singleton pattern — model loads only on first tool invocation.
* **Consequences**: Faster imports, easier testing (can monkeypatch before first call), no side effects on import.

## ADR-009: Import sandbox logic from linter.py in test_runner.py
* **Status**: Accepted
* **Context**: Both linter.py and test_runner.py need the same path validation logic.
* **Decision**: test_runner.py imports resolve_safe_path, UnsafePathError, and REPOSITORY_STORAGE_ROOT from linter.py.
* **Consequences**: Single source of truth for security logic. No code duplication. If sandbox logic needs updating, only one file changes.

## ADR-010: Step-by-step guided development process
* **Status**: Accepted
* **Context**: User preference for learning and understanding each component.
* **Decision**: Build module-by-module, verify each step with smoke tests before proceeding, user implements code manually.
* **Consequences**: Slower overall but deeper understanding. Catches bugs early. User owns the codebase.