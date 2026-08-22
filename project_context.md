# Project Context

**1. Project Vision**
An AI agent that can autonomously debug codebases. User uploads a repo and describes a bug or pastes a stack trace. The agent searches the codebase via RAG, analyzes the error, generates a code patch, validates it via linting and testing, and presents it for human approval.

**2. Constraints**
- Free tier only: Groq API for LLM inference (no paid APIs)
- Local embeddings: HuggingFace sentence-transformers/all-MiniLM-L6-v2 (no API embeddings)
- Vector DB: ChromaDB (local, no cloud)
- Python 3.14.6
- LangSmith account connected to GitHub for tracing
- Development approach: step-by-step, guided (not autonomous), small incremental steps
- Every code snippet must be reviewed for errors before delivery

**3. Technology Choices & Rationale**
| Technology | Choice | Reason |
|---|---|---|
| LLM | Groq (LLaMA) | Free tier, fast inference |
| Embeddings | all-MiniLM-L6-v2 | Local, free, good quality |
| Vector DB | ChromaDB | Local, simple, good LangChain integration |
| Code Chunking | RecursiveCharacterTextSplitter | Language-aware, preserves semantic boundaries |
| Encoding | charset-normalizer | Prevents silent byte-mangling |
| Linter | Ruff | Fast (Rust-based), static analysis only |
| Path handling | os.path.join | Cross-platform compatibility |
| Backend | FastAPI | Async, modern, auto-docs |
| Frontend | Streamlit | Rapid prototyping, built-in chat UI |
| Workflow | LangGraph | State machine for multi-step agent logic |

**4. Design Principles**
- Security first: sandbox all file access, validate all paths
- No cross-repository data leakage in vector search
- Lazy initialization of expensive resources (embeddings model)
- Encoding safety: never assume UTF-8
- Defensive error handling: warn and continue, don't crash
- Single source of truth for security logic (linter.py exports sandbox functions)

**5. Key Technical Decisions**
- Chunk size 1500 chars / 200 overlap: balances retrieval quality, context preservation, and token efficiency
- Single Chroma collection with mandatory repository_id filter (vs separate collection per repo): simpler to manage, but requires strict filtering
- _line_range_from_offset for mapping chunks to line numbers: immune to whitespace changes or duplicate line content
- Lazy singleton for vector store in code_search.py: avoids loading 90MB embedding model on import
- repository_id regex validation: prevents path traversal before the path is even constructed