# Walkthrough

A complete debugging session against a repository the platform had never seen,
captured verbatim on 24 August 2026. Nothing below is reconstructed: every
timing, patch, and test result is copied from the actual run.

## The subject

A small stock-valuation library, uploaded as a zip:

```
inventory/
├── inventory/
│   ├── __init__.py
│   └── stock.py          <- contains the bug
├── tests/
│   └── test_stock.py
├── requirements.txt
└── README.md
```

The bug is a plausible one, an operator slip that produces a wrong number
rather than a crash:

```python
def total_value(items):
    """Total monetary value of everything in stock."""
    return sum(item["price"] + item["quantity"] for item in items)   # + should be *
```

`stock.py` holds three functions and the repository has two packages, so
retrieval genuinely has to pick the right one. Baseline before starting:

```
tests/test_stock.py::test_total_value FAILED
tests/test_stock.py::test_count_units PASSED
tests/test_stock.py::test_low_stock  PASSED
```

## 1. Upload

The archive was produced by compressing a folder, so everything inside sits
under `inventory-main/`, exactly what GitHub's "Download ZIP" gives you.

```
POST /repositories/upload    (multipart: file=inventory.zip, repository_id=inventory)
201  {"repository_id": "inventory", "files_loaded": 5,
      "chunks_indexed": 5, "message": "Indexed 5 files into 5 chunks."}
```

On disk afterwards:

```
storage/repositories/inventory/
├── README.md
├── inventory/
├── requirements.txt
└── tests/
```

The wrapper directory is gone. That matters more than it looks: left in place,
every source file would sit one level below the root the tools resolve to, the
model would propose `inventory/stock.py`, validation would look for it at the
root, and every run would fail with "target file not found" for no visible
reason.

## 2. Start a session

```
POST /debug
{
  "repository_id": "inventory",
  "issue_description": "test_total_value fails. total_value returns 19.5 instead of
                        40.0 -- it looks like stock.py is adding price and quantity
                        instead of multiplying them."
}
```

**200 in 4.7 seconds.** In that time the agent parsed the issue, retrieved
matching code, read the dependency file, called the model twice, wrote a patch,
applied it behind a backup, ran three validators, and reverted the file.

The response is `awaiting_approval`: the graph is paused mid-execution,
checkpointed to SQLite, waiting for a human.

## 3. What came back

```
target_file : inventory/stock.py
patch_type  : snippet
```

```
<<<<<<< SEARCH
def total_value(items):
    """Total monetary value of everything in stock."""
    return sum(item["price"] + item["quantity"] for item in items)
=======
def total_value(items):
    """Total monetary value of everything in stock."""
    return sum(item["price"] * item["quantity"] for item in items)
>>>>>>> REPLACE
```

Validation:

```
✅ Syntax   Syntax check passed: 'inventory/stock.py' is valid python.
✅ Lint     Linting Passed: No issues found in 'inventory/stock.py'.
✅ Tests    Tests now pass (they were failing before the patch).

              tests/test_stock.py::test_total_value PASSED   [ 33%]
              tests/test_stock.py::test_count_units PASSED   [ 66%]
              tests/test_stock.py::test_low_stock  PASSED    [100%]
```

The tests line is the one worth reading twice. It does not say "tests pass";
it says they **were failing and now are not**, because the suite is run once
before the patch and once after. Without that comparison, a repository with
pre-existing failures is indistinguishable from a patch that did not work, and
the reported bug is usually itself a failing test.

The file on disk was untouched at this point. The patch existed only long
enough to be tested.

## 4. Request changes

Rather than approving, the reviewer asked for something more:

```
POST /sessions/a92587726a3a49bab20147d09f1766e4/approve
{"action": "revise",
 "feedback": "Good fix. Please also add a doctest-style example to the docstring."}
```

**200 in 2.4 seconds**, and critically the status came back as
**`awaiting_approval` again, not `completed`**. A revision is not a rejection:
the feedback goes back into the prompt, a new patch is generated, and the
session pauses once more.

```
<<<<<<< SEARCH
def total_value(items):
    """Total monetary value of everything in stock."""
    return sum(item["price"] + item["quantity"] for item in items)
=======
def total_value(items):
    """Total monetary value of everything in stock.

    >>> items = [{"price": 10.0, "quantity": 3}, {"price": 2.5, "quantity": 4}]
    >>> total_value(items)
    40.0
    """
    return sum(item["price"] * item["quantity"] for item in items)
>>>>>>> REPLACE
```

It kept the fix and added the example, with values consistent with the existing
test. The revised patch was validated from scratch: all three checks green
again.

## 5. Approve

```
POST /sessions/a92587726a3a49bab20147d09f1766e4/approve
{"action": "approve"}
```

```
Debugging session completed successfully.
Patch applied to inventory/stock.py.
Syntax check: Syntax check passed: 'inventory/stock.py' is valid python.
Lint result: Linting Passed: No issues found in 'inventory/stock.py'.
Tests: Tests now pass (they were failing before the patch).
```

The file on disk, for the first time in the session:

```python
def total_value(items):
    """Total monetary value of everything in stock.

    >>> items = [{"price": 10.0, "quantity": 3}, {"price": 2.5, "quantity": 4}]
    >>> total_value(items)
    40.0
    """
    return sum(item["price"] * item["quantity"] for item in items)
```

`pytest` confirms: 3 passed.

## Timings

| Step | Duration |
|---|---|
| Upload and index 5 files | under a second |
| `POST /debug`: retrieve, diagnose, patch, validate | 4.7 s |
| `POST /approve` with `revise`: regenerate and re-validate | 2.4 s |
| `POST /approve` with `approve`: apply | under a second |

Both slow calls are synchronous and block. The endpoints are plain `def`, not
`async def`, so FastAPI dispatches them to a threadpool; `/health` answers in
2 ms while a debug run is in flight.

## What this run demonstrates

**Retrieval picked the right file.** Two packages, three functions, and the
patch landed on the one that was wrong.

**The patch was verified before a human saw it.** Syntax, lint, and a real
pytest run against the patched file, then reverted. The reviewer is deciding
about a change that has already been shown to work, not guessing.

**The test signal is comparative, not absolute.** "Were failing, now pass" is a
different claim from "pass", and only the first one means anything.

**The loop is a loop.** The reviewer asked for more and got a new patch, not a
dead session. Nothing touched the working tree until approval.

## What this run does not demonstrate

Being honest about the boundaries of a single happy-path capture:

- **The retry path never fired.** The first patch was correct. Retries on
  syntax errors, unapplied patches, invented filenames, and failing tests are
  covered by the test suites, not by this run.
- **One file, one function.** Bugs spanning several files are outside what
  `target_file` currently expresses.
- **The bug was described accurately.** The issue text named the file and the
  operator. A vaguer report is a harder problem, and this run says nothing
  about it.
- **A small repository.** Five files. Retrieval quality on a codebase of
  thousands of chunks is untested.

## Reproducing this

```bash
uvicorn backend.api.main:app --reload --reload-dir backend --port 8000   # terminal 1
streamlit run frontend/app.py                                            # terminal 2
```

`--reload-dir backend` is not optional: a bare `--reload` watches
`storage/repositories/`, so every upload restarts the server and reloads the
embedding model, and the request after an upload times out.

Then upload a zipped repository, describe a bug, and review what comes back.