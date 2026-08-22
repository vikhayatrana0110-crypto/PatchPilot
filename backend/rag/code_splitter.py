import logging
from typing import List, Optional
from langchain_core.documents import Document
from langchain_text_splitters import Language, RecursiveCharacterTextSplitter

logger = logging.getLogger(__name__)

CHUNK_SIZE: int = 1500
CHUNK_OVERLAP: int = 200

EXTENSION_TO_LANGUAGE: dict[str, Language] = {
    ".py":  Language.PYTHON,
    ".js":  Language.JS,
    ".ts":  Language.TS,
    ".java": Language.JAVA,
    ".cpp": Language.CPP,
    ".c":   Language.CPP,
    ".h":   Language.CPP,
    ".md":  Language.MARKDOWN,
}


class CodeSplitter:
    def __init__(self, chunk_size: int = CHUNK_SIZE, chunk_overlap: int = CHUNK_OVERLAP):
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        self._splitter_cache: dict = {}

    def _get_splitter(self, language: Optional[Language]) -> RecursiveCharacterTextSplitter:
        key = language if language is not None else "generic"
        if key not in self._splitter_cache:
            if language is not None:
                self._splitter_cache[key] = RecursiveCharacterTextSplitter.from_language(
                    language=language,
                    chunk_size=self.chunk_size,
                    chunk_overlap=self.chunk_overlap,
                    add_start_index=True,   # <-- key fix: track exact char offset
                )
            else:
                self._splitter_cache[key] = RecursiveCharacterTextSplitter(
                    chunk_size=self.chunk_size,
                    chunk_overlap=self.chunk_overlap,
                    add_start_index=True,   # <-- key fix
                )
        return self._splitter_cache[key]

    def split(self, documents: List[Document], repository_id: str = "") -> List[Document]:
        all_chunks: List[Document] = []

        for doc in documents:
            ext = doc.metadata.get("file_extension", "")
            language = EXTENSION_TO_LANGUAGE.get(ext)
            splitter = self._get_splitter(language)
            file_path = doc.metadata.get("file_path", "unknown")

            try:
                chunks = splitter.split_documents([doc])
            except Exception as e:
                logger.warning("Failed to split %s: %s", file_path, e, exc_info=True)
                continue

            if not chunks:
                logger.debug("No chunks produced for %s — file may be empty.", file_path)
                continue

            for chunk in chunks:
                start_line, end_line = _line_range_from_offset(
                    doc.page_content, chunk.metadata.get("start_index"), chunk.page_content
                )
                chunk.metadata.update({
                    "repository_id": repository_id,
                    "language": ext.lstrip(".") if ext else "unknown",
                    "start_line": start_line,
                    "end_line": end_line,
                })
                # start_index was only needed to derive start_line/end_line;
                # drop it so it doesn't leak confusing raw-offset data downstream
                chunk.metadata.pop("start_index", None)
                all_chunks.append(chunk)

        logger.info(
            "Split %d documents into %d chunks (chunk_size=%d, overlap=%d).",
            len(documents), len(all_chunks), self.chunk_size, self.chunk_overlap,
        )
        return all_chunks


def _line_range_from_offset(
    full_text: str, start_index: Optional[int], chunk_content: str
) -> tuple[int, int]:
    """
    Derive 1-indexed (start_line, end_line) from a character offset into the
    original document. Immune to whitespace-stripping and duplicate-line
    ambiguity, unlike matching on the chunk's first line of text.
    Returns (0, 0) if no offset is available (e.g. generic splitter fallback
    that doesn't support add_start_index for some reason).
    """
    if start_index is None:
        return 0, 0

    start_line = full_text.count("\n", 0, start_index) + 1
    num_newlines_in_chunk = chunk_content.count("\n")
    end_line = start_line + num_newlines_in_chunk
    return start_line, end_line