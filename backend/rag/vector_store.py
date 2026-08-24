import os
import logging
from typing import List, Optional
from langchain_core.documents import Document
from langchain_chroma import Chroma
from langchain_huggingface import HuggingFaceEmbeddings

from backend import resolve_project_path

logger = logging.getLogger(__name__)

# Anchored to the project root, not the CWD, so the index is the same one
# regardless of where the process was launched from.
PERSIST_DIRECTORY: str = str(resolve_project_path(os.getenv("CHROMA_PERSIST_DIR", "./storage/vector_indexes")))
EMBEDDING_MODEL: str = os.getenv("EMBEDDING_MODEL", "sentence-transformers/all-MiniLM-L6-v2")


class CodeVectorStore:
    def __init__(
        self,
        persist_directory: str = PERSIST_DIRECTORY,
        embedding_model_name: str = EMBEDDING_MODEL,
        collection_name: str = "codebase_index"
    ):
        self.persist_directory = persist_directory
        self.collection_name = collection_name

        os.makedirs(self.persist_directory, exist_ok=True)

        try:
            self.embeddings = HuggingFaceEmbeddings(model_name=embedding_model_name)
        except Exception as e:
            logger.error("Failed to initialize embeddings model: %s", e, exc_info=True)
            raise ValueError(f"Could not load embedding model {embedding_model_name}") from e

        self.vectorstore = Chroma(
            collection_name=self.collection_name,
            embedding_function=self.embeddings,
            persist_directory=self.persist_directory
        )

    def add_documents(self, documents: List[Document]) -> None:
        """Add documents (chunks) to the vector store."""
        if not documents:
            logger.warning("No documents provided to add_documents. Skipping.")
            return

        missing_repo_id = [
            d.metadata.get("file_path", "unknown") for d in documents
            if not d.metadata.get("repository_id")
        ]
        if missing_repo_id:
            logger.warning(
                "%d documents are missing 'repository_id' metadata (e.g. %s). "
                "These will not be filterable by repository and may leak across "
                "repositories in search results.",
                len(missing_repo_id), missing_repo_id[:3]
            )

        try:
            self.vectorstore.add_documents(documents)
            logger.info("Successfully added %d documents to the vector store.", len(documents))
        except Exception as e:
            logger.error("Failed to add documents to vector store: %s", e, exc_info=True)
            raise

    def search(
        self,
        query: str,
        repository_id: str,
        k: int = 5,
        extra_filter: Optional[dict] = None,
    ) -> List[Document]:
        """
        Search for the most relevant chunks matching the query, scoped to a
        single repository.

        repository_id is required (not optional) because this vector store
        uses a single shared Chroma collection across all repositories.
        Without a mandatory repository scope, a query could silently return
        chunks from an unrelated repository -- there is nothing else keeping
        results isolated.

        extra_filter lets you add additional metadata constraints (e.g.
        {"language": "py"}), which are combined with the repository_id
        filter using Chroma's $and syntax.
        """
        if not query.strip():
            logger.warning("Empty search query provided. Returning empty list.")
            return []
        if not repository_id:
            raise ValueError("repository_id is required to prevent cross-repository search results.")

        combined_filter: dict = {"repository_id": repository_id}
        if extra_filter:
            combined_filter = {"$and": [{"repository_id": repository_id}, extra_filter]}

        try:
            docs = self.vectorstore.similarity_search(
                query=query,
                k=k,
                filter=combined_filter
            )
            logger.info(
                "Retrieved %d documents for query '%s' in repository '%s'.",
                len(docs), query, repository_id
            )
            return docs
        except Exception as e:
            logger.error("Search failed for query '%s': %s", query, e, exc_info=True)
            return []

    def get_retriever(self, repository_id: str, k: int = 5):
        """Return a standard LangChain retriever interface, scoped to a single repository."""
        if not repository_id:
            raise ValueError("repository_id is required to prevent cross-repository search results.")
        return self.vectorstore.as_retriever(
            search_kwargs={"k": k, "filter": {"repository_id": repository_id}}
        )


    def delete_repository(self,repository_id: str) -> int:
        """
        Remove every chunk belonging to one repository.
        Needed before re-indexing an upload. Without it a second upload of the
        same repository_id leaves the previous version's chunks in the
        collection, and retrieval silently mixes two versions of one file.
        `clear()` is not a substitute -- it wipes every repository.
        """

        if not repository_id:
            raise ValueError("Repository_id is required.")

        collection = self.vectorstore._collection
        existing = collection.get(where={"repository_id": repository_id},include=[])

        ids = existing.get("ids") or []
        if ids: 
            collection.delete(ids=ids)
        logger.info("Deleted %d chunks for repository '%s'", len(ids), repository_id)

        return len(ids)
    
    def clear(self) -> None:
        """Clear all documents from the collection (all repositories)."""
        try:
            self.vectorstore.delete_collection()
            self.vectorstore = Chroma(
                collection_name=self.collection_name,
                embedding_function=self.embeddings,
                persist_directory=self.persist_directory
            )
            logger.info("Cleared the vector store collection '%s'.", self.collection_name)
        except Exception as e:
            logger.error("Failed to clear vector store: %s", e, exc_info=True)
            raise


_store: Optional["CodeVectorStore"] = None

def get_vector_store() -> "CodeVectorStore":
    """
    process wide lazy singleton for vector store.
    both the search tool and upload endpoint share this one
    """

    global _store
    if _store is None:
        _store = CodeVectorStore()
    return _store