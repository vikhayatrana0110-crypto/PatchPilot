"""Each of the six agent tools, exercised in isolation.

No Groq calls here. Every tool is deterministic given a repository on disk, so
this suite runs in seconds and fails for real reasons rather than rate limits.
"""

import pytest

from backend.tools.code_search import search_codebase
from backend.tools.dependency_inspector import inspect_dependencies
from backend.tools.linter import (
    UnsafePathError,
    resolve_repository_root,
    resolve_safe_path,
    run_linter,
    run_syntax_check,
)
from backend.tools.stack_trace import analyze_stack_trace
from backend.tools.test_runner import run_unit_tests

BROKEN_SYNTAX = "def add(a, b)\n    return a + b\n"
FIXED = '"""Arithmetic helpers."""\n\n\ndef add(a, b):\n    return a + b\n\n\ndef multiply(a, b):\n    return a * b\n'


# --------------------------------------------------------------------------
# Path resolution: the boundary every other tool depends on
# --------------------------------------------------------------------------

class TestPathResolution:
    def test_valid_id_resolves_under_the_storage_root(self, repo_id):
        root = resolve_repository_root(repo_id)
        assert root.name == repo_id
        assert root.is_dir()

    @pytest.mark.parametrize("bad", ["../etc", "a/b", "", "has space", "semi;colon"])
    def test_hostile_ids_are_rejected(self, bad):
        with pytest.raises(UnsafePathError):
            resolve_repository_root(bad)

    def test_absolute_file_paths_are_rejected(self, repo_id):
        with pytest.raises(UnsafePathError):
            resolve_safe_path(repo_id, "/etc/passwd")

    def test_traversal_out_of_the_repo_is_rejected(self, repo_id):
        with pytest.raises(UnsafePathError):
            resolve_safe_path(repo_id, "../../../etc/passwd")

    def test_non_python_files_are_rejected(self, repo_id):
        with pytest.raises(UnsafePathError):
            resolve_safe_path(repo_id, "requirements.txt")

    def test_missing_file_raises_not_found(self, repo_id):
        with pytest.raises(FileNotFoundError):
            resolve_safe_path(repo_id, "nope.py")


# --------------------------------------------------------------------------
# run_syntax_check
# --------------------------------------------------------------------------

class TestSyntaxCheck:
    def test_valid_file_passes(self, repo_id):
        assert run_syntax_check.invoke(
            {"repository_id": repo_id, "file_path": "calculator.py"}
        ).startswith("Syntax check passed")

    def test_broken_file_reports_the_error(self, repo_id, calculator):
        calculator.write_text(BROKEN_SYNTAX)
        result = run_syntax_check.invoke(
            {"repository_id": repo_id, "file_path": "calculator.py"}
        )
        assert result.startswith("Syntax error")

    def test_bad_repository_id_returns_an_error_rather_than_raising(self):
        result = run_syntax_check.invoke(
            {"repository_id": "../etc", "file_path": "calculator.py"}
        )
        assert result.startswith("Error:")


# --------------------------------------------------------------------------
# run_linter
# --------------------------------------------------------------------------

class TestLinter:
    def test_clean_file_passes(self, repo_id):
        assert run_linter.invoke(
            {"repository_id": repo_id, "file_path": "calculator.py"}
        ).startswith("Linting Passed")

    def test_lint_problems_are_reported(self, repo_id, calculator):
        calculator.write_text("import os\nimport sys\n\n\ndef add(a, b):\n    return a + b\n")
        result = run_linter.invoke(
            {"repository_id": repo_id, "file_path": "calculator.py"}
        )
        assert result.startswith("Linting Issues")


# --------------------------------------------------------------------------
# run_unit_tests
# --------------------------------------------------------------------------

class TestUnitTests:
    def test_failing_suite_is_reported(self, repo_id):
        result = run_unit_tests.invoke(
            {"repository_id": repo_id, "test_file_path": "tests/test_calculator.py"}
        )
        assert result.startswith("Test Failiures")

    def test_passing_suite_is_reported(self, repo_id, calculator):
        calculator.write_text(FIXED)
        result = run_unit_tests.invoke(
            {"repository_id": repo_id, "test_file_path": "tests/test_calculator.py"}
        )
        assert result.startswith("Tests Passed")

    def test_stale_bytecode_cannot_fake_a_pass(self, repo_id, repo_root, calculator):
        """The regression that mattered most.

        Python validates a .pyc by source mtime AND size, and 'a + b' and
        'a - b' are the same size. Without the cache controls in test_runner,
        the fixed bytecode from the first run survives the revert and the second
        run reports PASSED for code that is no longer on disk.
        """
        calculator.write_text(FIXED)
        assert run_unit_tests.invoke(
            {"repository_id": repo_id, "test_file_path": "tests/test_calculator.py"}
        ).startswith("Tests Passed")

        calculator.write_text(calculator.read_text().replace("a + b", "a - b"))
        result = run_unit_tests.invoke(
            {"repository_id": repo_id, "test_file_path": "tests/test_calculator.py"}
        )
        assert result.startswith("Test Failiures"), "stale bytecode won"

    def test_no_cache_directories_are_left_behind(self, repo_id, repo_root):
        run_unit_tests.invoke(
            {"repository_id": repo_id, "test_file_path": "tests/test_calculator.py"}
        )
        assert not list(repo_root.rglob("__pycache__"))
        assert not (repo_root / ".pytest_cache").exists()


# --------------------------------------------------------------------------
# analyze_stack_trace
# --------------------------------------------------------------------------

TRACEBACK = '''Traceback (most recent call last):
  File "/app/main.py", line 42, in run
    total = compute(values)
  File "/app/calc.py", line 17, in compute
    return sum(v) / len(v)
ZeroDivisionError: division by zero
'''


class TestStackTrace:
    def test_error_type_and_frames_are_extracted(self):
        result = analyze_stack_trace.invoke({"stack_trace": TRACEBACK})
        assert "ZeroDivisionError" in result
        assert "calc.py" in result

    def test_plain_prose_does_not_crash(self):
        result = analyze_stack_trace.invoke(
            {"stack_trace": "the totals come out wrong when the list is empty"}
        )
        assert isinstance(result, str) and result


# --------------------------------------------------------------------------
# inspect_dependencies
# --------------------------------------------------------------------------

class TestDependencies:
    def test_requirements_are_found(self, repo_id):
        result = inspect_dependencies.invoke({"repository_id": repo_id})
        assert "requirements.txt" in result
        assert "pytest" in result

    def test_hostile_id_is_rejected(self):
        """Takes an id, not a path. Before that change it would read any directory."""
        result = inspect_dependencies.invoke({"repository_id": "../etc"})
        assert result.startswith("error:")


# --------------------------------------------------------------------------
# search_codebase
# --------------------------------------------------------------------------

class TestCodeSearch:
    def test_unindexed_repository_reports_no_results(self, repo_id):
        """The fixture repo is never indexed, so this must not invent matches."""
        result = search_codebase.invoke(
            {"query": "add two numbers", "repository_id": repo_id, "k": 3}
        )
        assert "No relevant code found" in result

    def test_missing_repository_id_is_an_error(self):
        result = search_codebase.invoke({"query": "anything", "repository_id": "", "k": 3})
        assert result.startswith("Error:")
