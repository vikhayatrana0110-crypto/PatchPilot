import os
import re
import sys
import subprocess
import logging
from pathlib import Path
from langchain_core.tools import tool

from backend import resolve_project_path

logger = logging.getLogger(__name__)

SAFE_REPOSITORY_ID = re.compile(r"^[A-Za-z0-9_-]+$")

# Anchored to the project root, not the CWD -- uvicorn started from another
# directory would otherwise resolve this somewhere else entirely and report
# every repository as missing.
REPOSITORY_STORAGE_ROOT = resolve_project_path(os.getenv("REPO_STORAGE_ROOT", "./storage/repositories"))

class UnsafePathError(ValueError):
    """Raised when a requested file path escapes the allowed repository sandbox."""

def resolve_repository_root(repository_id: str) -> Path:
    """
    Resolve the on-disk root of a repository from its id.

    This is the ONLY place a repository_id becomes a path. Every caller that
    needs a repo root derives it from here rather than carrying its own copy --
    otherwise a node can write a patch to one directory while the validation
    tools read another, and the mismatch surfaces as validation passing against
    code that was never patched.
    """
    if not repository_id:
        raise UnsafePathError("repository_id is required.")

    if not SAFE_REPOSITORY_ID.match(repository_id):
        raise UnsafePathError(f"Invalid repository_id '{repository_id}': must contain only " "letters, digits, underscores, and hyphens.")

    repo_root = (REPOSITORY_STORAGE_ROOT / repository_id).resolve()

    # catches unexpected case like symlinks placed directly under storage root

    try:
        repo_root.relative_to(REPOSITORY_STORAGE_ROOT)

    except ValueError:
        raise UnsafePathError(f"Invalid repository_id '{repository_id}'.")

    return repo_root


def resolve_safe_path(repository_id: str, file_path: str) -> Path:
    """
    Resolve file_path against REPOSITORY_STORAGE_ROOT/<repository_id>/ and
    verify the final, symlink-resolved path is actually contained within it.
 
    file_path is treated as relative to the repository root -- callers
    should not (and cannot usefully) pass an absolute host path.
    """
    repo_root = resolve_repository_root(repository_id)

    # Reject absolute paths outright -- they have no valid meaning here and
    # otherwise os.path.join would silently discard the repo_root prefix.

    if os.path.isabs(file_path):
        raise UnsafePathError(f"file_path must be realtive to the repo, got absolute path: {file_path}")
    
    candidate = (repo_root / file_path).resolve()

    try:
        candidate.relative_to(repo_root)
    except ValueError:
        raise UnsafePathError(f"file_path '{file_path}' resolves outside the repository sandbox.")

    if not candidate.is_file():
        raise FileNotFoundError(f"file not found in repo: {file_path}")

    if candidate.suffix != ".py":
        raise UnsafePathError(f"only .py files are supported, got: {file_path}")

    return candidate

@tool
def run_syntax_check(repository_id: str,file_path: str) -> str:
    """
    Checks if a Python file has valid syntax using 'python -m py_compile'.
    
    Args:
        repository_id: The ID of the repository the file belongs to.
        file_path: The absolute path to the Python file to check.
        (e.g. "src/app.py"). Must not be an absolute path.
        
    Returns:
        A string indicating success or containing the syntax error details.
    """
    logger.info("Agent invoked run_syntax_check on '%s' in repo '%s'", file_path, repository_id)

    try:
        safe_path = resolve_safe_path(repository_id,file_path)

    except UnsafePathError as e:
        return f"Error: {e}"
    except FileNotFoundError as e:
        return f"Error: {e}"
    
    try:
        result = subprocess.run(
            [sys.executable,"-m","py_compile",str(safe_path)],
            capture_output=True,
            text=True,
            timeout=10
        )

        if result.returncode == 0:
            return f"Syntax check passed: '{file_path}' is valid python."
        else:
            return f"Syntax error in '{file_path}':\n{result.stderr}"

    except subprocess.TimeoutExpired:
        return "Error: Syntax check timed out after 10 sec."
    except Exception as e:
        logger.error("Syntax check failed: %s",e,exc_info=True)
        return f"Error running syntax check: {str(e)}"

@tool
def run_linter(repository_id: str,file_path: str) -> str:
    """
    Runs the 'ruff' linter on a specific Python file to find logical errors, 
    unused imports, or bad practices.
    
    Args:
        repository_id: The ID of the repository the file belongs to.
        file_path: The absolute path to the Python file to check.
        (e.g. "src/app.py"). Must not be an absolute path.
    
    Returns:
        A string containing the linter output, or a success message if no issues are found.
    """
    logger.info("Agent invoked run_linter on: '%s' in repo '%s'", file_path, repository_id)

    try:
        safe_path = resolve_safe_path(repository_id, file_path)
    except UnsafePathError as e:
        return f"Error: {e}"
    except FileNotFoundError as e:
        return f"Error: {e}"
 
    try:
        result = subprocess.run(
            [sys.executable, "-m", "ruff", "check", str(safe_path)],
            capture_output=True,
            text=True,
            timeout=10
        )
 
        if result.returncode == 0:
            return f"Linting Passed: No issues found in '{file_path}'."
        else:
            output = result.stdout.strip() or result.stderr.strip()
            return f"Linting Issues in '{file_path}':\n{output}"
 
    except subprocess.TimeoutExpired:
        return "Error: Linter timed out after 10 seconds."
    except Exception as e:
        logger.error("Linter failed: %s", e, exc_info=True)
        return f"Error running linter (is ruff installed?): {str(e)}"