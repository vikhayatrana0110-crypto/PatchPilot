import os
from typing import List , Set, Optional
from langchain_core.documents import Document 
import logging
from charset_normalizer import from_path

logger = logging.getLogger(__name__)

# Directories to skip when traversing a repository
DEFAULT_IGNORE_DIRS: Set[str] = {
    ".git", ".venv", "venv", "__pycache__",
    "node_modules", ".idea", ".vscode",
    "build", "dist", "storage", ".mypy_cache",
    ".pytest_cache", "htmlcov"
}

# Supported file extensions to ingest
SUPPORTED_EXTENSIONS: Set[str] = {
    ".py", ".js", ".ts", ".java", ".cpp", ".c",
    ".h", ".sql", ".json", ".yaml", ".yml",
    ".toml", ".md", ".txt"
}

#max size files to read - 10 MB guard
MAX_FILE_SIZE_BYTES: int = 10 * 1024 * 1024

class RepositoryLoader:
    def __init__(
            self,
            repo_path: str,
            ignore_dirs: Optional[Set[str]] = None,
            max_file_size: int = MAX_FILE_SIZE_BYTES
    ):
        self.repo_path = os.path.abspath(repo_path)
        
        if not os.path.isdir(self.repo_path):
            raise ValueError(f"Repository path does not exist: {self.repo_path}")

        self.ignore_dirs = ignore_dirs if ignore_dirs is not None else DEFAULT_IGNORE_DIRS
        self.max_file_size = max_file_size
    
    def load(self) -> List[Document]:
        """crawl repo -> return list of documents"""
        documents=[]
        skipped_by_extension = 0

        for root,dirs,files in os.walk(self.repo_path, followlinks = False):
            #prune ignored dirs in place
            dirs[:] = [d for d in dirs if d not in self.ignore_dirs]

            for file in files:
                ext = os.path.splitext(file)[1].lower()
                if ext not in SUPPORTED_EXTENSIONS:
                    skipped_by_extension += 1  # ✅ INCREMENT HERE
                    continue

                file_path = os.path.join(root,file)
                rel_path = os.path.relpath(file_path,self.repo_path).replace(os.sep,"/")


                # file size guard
                try:
                    file_size = os.path.getsize(file_path)
                except OSError as e:
                    logger.warning("cannot read file size for %s :%s", rel_path, e, exc_info=True)
                    continue

                if file_size > self.max_file_size:
                    logger.warning(
                        "Skipping %s — file size %d bytes exceeds limit of %d bytes.",
                        rel_path, file_size, self.max_file_size
                    )
                    continue

                # encoding aware file reading
                try:
                    result = from_path(file_path).best()
                    if result is None:
                        logger.warning("could not detect encoding for %s - skipping.",rel_path)
                        continue
                    content = str(result)
                except Exception as e:
                    logger.warning("failed to read %s: %s",rel_path,e,exc_info=True)
                    continue

                doc = Document(
                    page_content=content,
                    metadata={
                        "file_path": rel_path,
                        "file_name": file,
                        "file_extension":  ext
                    }
                )
                documents.append(doc)
        if skipped_by_extension > 0:
            logger.debug("Skipped %d files with unsupported extensions.", skipped_by_extension)
        logger.info("Loaded %d documents from  repository: %s", len(documents), self.repo_path)
        return documents

    