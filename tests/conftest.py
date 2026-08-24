"""Shared fixtures.

The tools resolve every path from REPOSITORY_STORAGE_ROOT, so a fixture
repository has to live there rather than in a tmp_path. Each test session gets
a uniquely named one and removes it afterwards, which keeps the suite from
depending on (or damaging) whatever repositories a developer has uploaded.
"""

import shutil
import uuid

import pytest

from backend import PROJECT_ROOT
from backend.tools.linter import REPOSITORY_STORAGE_ROOT

GOOD_SOURCE = '''"""Arithmetic helpers."""


def add(a, b):
    return a - b


def multiply(a, b):
    return a * b
'''

TESTS_SOURCE = '''from calculator import add, multiply


def test_add():
    assert add(2, 3) == 5


def test_multiply():
    assert multiply(2, 3) == 6
'''


@pytest.fixture(scope="session")
def repo_id():
    """A throwaway repository under the storage root, removed after the session."""
    rid = f"pytest_{uuid.uuid4().hex[:8]}"
    root = REPOSITORY_STORAGE_ROOT / rid
    (root / "tests").mkdir(parents=True)

    (root / "calculator.py").write_text(GOOD_SOURCE)
    (root / "tests" / "test_calculator.py").write_text(TESTS_SOURCE)
    (root / "requirements.txt").write_text("pytest>=8.0.0\n")
    (root / "conftest.py").write_text("")

    yield rid

    shutil.rmtree(root, ignore_errors=True)


@pytest.fixture
def repo_root(repo_id):
    return REPOSITORY_STORAGE_ROOT / repo_id


@pytest.fixture
def calculator(repo_root):
    """The buggy source file, restored to its original state after each test."""
    path = repo_root / "calculator.py"
    original = path.read_text()
    yield path
    path.write_text(original)
