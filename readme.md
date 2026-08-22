# Agentic Codebase Debugging Platform

![License](https://img.shields.io/badge/license-MIT-blue.svg)
![Python](https://img.shields.io/badge/python-3.14.6-blue.svg)
![Status](https://img.shields.io/badge/status-active-success.svg)

An AI-powered platform that automatically debugs codebases. Users upload a repository and describe a bug (or paste a stack trace). An LLM agent then searches the codebase, analyzes the error, generates a patch, validates it, and presents the fix for human approval.

## 🌟 Key Features

- **Repository Ingestion:** Robust file parsing with automatic encoding detection using `charset-normalizer`.
- **Language-Aware Chunking:** Smart code splitting (1500 chars, 200 overlap) that preserves class and function boundaries.
- **Secure Vector Search:** Context-aware semantic search via ChromaDB and HuggingFace embeddings (`all-MiniLM-L6-v2`). Mandatory `repository_id` scoping prevents cross-repo data leakage.
- **Agentic Tools:** 6 specialized LangChain tools for the AI agent:
  - `search_codebase`
  - `analyze_stack_trace`
  - `inspect_dependencies`
  - `run_syntax_check`
  - `run_linter` (Ruff)
  - `run_unit_tests`
- **Security First:** Strict sandboxing with path traversal prevention using `pathlib.relative_to()`, regex validation for repository IDs, and rejection of absolute paths.

## 🏗️ Architecture Overview

The platform uses a modern AI stack powered by LangChain and LangGraph for agent workflows.

- **Backend:** FastAPI for API orchestration.
- **AI Agent:** LangGraph state machine orchestrating LLaMA models (via Groq API).
- **RAG & Storage:** ChromaDB for vector persistence, HuggingFace for embeddings.
- **Frontend:** Streamlit UI featuring an interactive diff viewer for human-in-the-loop patch approval.

### Directory Structure

```text
CODEBASE DEBUGGING PLATFORM/
├── backend/
│   ├── rag/
│   │   ├── __init__.py
│   │   ├── repository_loader.py    # Crawls repo, reads files
│   │   ├── code_splitter.py        # Language-aware chunking
│   │   └── vector_store.py         # ChromaDB + embeddings
│   ├── tools/
│   │   ├── __init__.py
│   │   ├── code_search.py          # search_codebase tool
│   │   ├── stack_trace.py          # analyze_stack_trace tool
│   │   ├── dependency_inspector.py # inspect_dependencies tool
│   │   ├── linter.py               # Syntax check & linter
│   │   └── test_runner.py          # run_unit_tests
│   ├── graph/                      # (Planned) LangGraph workflow
│   │   ├── state.py
│   │   ├── nodes.py
│   │   └── workflow.py
│   └── main.py                     # (Planned) FastAPI entry
├── frontend/
│   └── app.py                      # (Planned) Streamlit UI
├── storage/
│   ├── vector_indexes/             # ChromaDB persistence
│   └── repositories/               # Uploaded repo sandboxes
├── requirements.txt
├── tasks.md                        # Task checklist and implementation progress
├── .env.example
└── .env                            # (User creates from .env.example)
```

## 🚀 Setup Instructions

### Prerequisites
- Python 3.14.6

### Installation

1. **Clone the repository:**
   ```bash
   git clone <repository_url>
   cd "CODEBASE DEBUGGING PLATFORM"
   ```

2. **Create and activate a virtual environment:**
   ```bash
   python -m venv venv
   # On Windows:
   venv\Scripts\activate
   # On macOS/Linux:
   source venv/bin/activate
   ```

3. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

4. **Environment Setup:**
   Copy the example environment file and configure your API keys.
   ```bash
   cp .env.example .env
   ```
   Edit `.env` and add your keys:
   ```env
   GROQ_API_KEY=your_groq_api_key
   LANGSMITH_API_KEY=your_langsmith_api_key
   ```

## 💻 Usage

*(Note: API and UI layers are currently in development)*

1. Upload your repository through the Streamlit interface or API endpoint.
2. Provide a bug description or paste a failing stack trace.
3. The agent will analyze the codebase, run tests, and propose a diff.
4. Review the diff in the UI and approve the patch.

## 🔒 Security Notes

- **Data Isolation:** All vector search queries require a `repository_id` to ensure no data bleeds between different user repositories.
- **Path Sandboxing:** Tools attempting file reads/writes strictly validate paths. Absolute paths are rejected, and `..` directory traversals are blocked.
- **Execution Sandboxing:** Linter and test runners operate in isolated environments.

## 🗺️ Roadmap

- [x] RAG ingestion pipeline and smart chunking
- [x] Vector store integration (ChromaDB)
- [x] Core debugging tools implementation
- [ ] Multi-step reasoning workflow using LangGraph
- [ ] FastAPI backend for robust API serving
- [ ] Streamlit UI with human-in-the-loop review system

## 📄 License

[MIT License](LICENSE) (Placeholder)