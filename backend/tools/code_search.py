import logging
from langchain_core.tools import tool
from backend.rag.vector_store import CodeVectorStore

logger = logging.getLogger(__name__)

# Lazy singleton: the embedding model and Chroma collection are only loaded
# the first time the tool is actually invoked, not merely on import.
# Why this matters:
# - Importing this module (e.g. during test collection, or when another
#   module imports it just to reuse an unrelated helper) used to trigger a
#   real embedding-model load and Chroma directory creation as a side effect,
#   even if the tool was never called.
# - It also made the module hard to test: any test file had to monkeypatch
#   HuggingFaceEmbeddings *before* importing this module, which is fragile
#   and breaks if some other module imports it first.
_vector_store: CodeVectorStore | None = None


def _get_vector_store() -> CodeVectorStore:
    global _vector_store
    if _vector_store is None:
        _vector_store = CodeVectorStore()
    return _vector_store


@tool
def search_codebase(query: str, repository_id: str, k: int = 5) -> str:
    """
    Searches the indexed codebase for relevant code snippets, functions, classes, or imports.

    Args:
        query: The search string to look for in the codebase.
        repository_id: The ID of the repository to search within.
        k: The number of results to return (default: 5).

    Returns:
        A formatted string containing the relevant code chunks.
    """
    logger.info("Agent invoked search_codebase with query: '%s' for repo: '%s'", query, repository_id)

    try:
        store = _get_vector_store()
        results = store.search(
            query=query,
            repository_id=repository_id,
            k=k
        )
    except ValueError as e:
        return f"Error: {str(e)}"
    except Exception:
        logger.error("Tool search_codebase failed", exc_info=True)
        return "Error: An internal database error occurred while searching the codebase."

    if not results:
        return f"No relevant code found in repository '{repository_id}' for query: '{query}'."

    formatted_results = []
    for i, doc in enumerate(results):
        file_path = doc.metadata.get("file_path", "unknown")
        sline = doc.metadata.get("start_line", "?")
        eline = doc.metadata.get("end_line", "?")
        content = doc.page_content
        formatted_results.append(
            f"--- Result {i+1} | File: {file_path} (Lines {sline}-{eline}) ---\n{content}\n"
        )

    return "\n".join(formatted_results)