import sys
import subprocess
import logging
from langchain_core.tools import tool
from backend.tools.linter import resolve_safe_path, UnsafePathError, REPOSITORY_STORAGE_ROOT

logger = logging.getLogger( __name__)

@tool
def run_unit_tests(repository_id: str, test_file_path: str) -> str:
    """
    Executes a specific pytest file to validate if a bug fix was successful.
    Args:
        repository_id: The ID of the repository.
        test_file_path: Path to the test file, relative to the repository root
            (e.g. "tests/test_app.py"). Must not be an absolute path.
    Returns:
        The pytest execution output, indicating passes/failures.
    """    
    logger.info("Agent invoked run_unit_tests on : '%s' in repo '%s'", test_file_path, repository_id)

    try:
        safe_path = resolve_safe_path(repository_id, test_file_path)

    except UnsafePathError as e:
        return f"Error: {e}"
    except FileNotFoundError as e:
        return f"Error: {e}"

    try:
        repo_root = (REPOSITORY_STORAGE_ROOT / repository_id).resolve()

        result = subprocess.run(
            [sys.executable,"-m","pytest",str(safe_path),"-v","--no-header"],
            capture_output=True,
            text=True,
            timeout=30,
            cwd=str(repo_root)
        )

        output = result.stdout.strip() or result.stderr.strip()

        if result.returncode == 0:
            return f"Tests Passed Succesfully:\n{output}"
        else:
            return f"Test Failiures Detected:\n{output}"

    except subprocess.TimeoutExpired:
        return  "Error: Test execution timed out after 30 sec.(possible infinite loop?)"
    except Exception as e:
        logger.error("Test execution failed: %s", e, exc_info=True)
        return f"Error running tests: {str(e)}"