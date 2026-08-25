# PatchPilot

Point it at a repo, describe a bug, and it writes a patch. Then it tests the patch before you ever see it, so you're approving something that already works instead of guessing.

Built on Groq's free tier. No paid API keys anywhere.

## What makes this different from pasting code into a chatbot

It's the same model you'd get in a browser. `openai/gpt-oss-120b` on Groq, free, nothing special. So the model isn't the point.

Two things a chat window can't do:

It searches your actual codebase. If the repo is bigger than what you can paste, or you don't know which file is broken, semantic search finds the relevant chunks for you.

It checks its own work. The agent applies its patch to a real file, runs `py_compile`, runs `ruff`, runs your `pytest` suite, then reverts the file. You see the results before you decide. If the patch breaks something, the agent notices and tries again with the error in its prompt.

That second part is the whole reason this exists. A chatbot hands you a confident suggestion and no idea whether it compiles.

## Careful: this runs code from the repo you give it

`pytest` imports `conftest.py` automatically. If you upload a repo with something nasty in there, it runs as you, with your file access. I tested this and confirmed it.

So: only point this at repos you trust, and keep it on localhost. There's no execution sandbox. That's a deliberate scope decision, not an oversight, but it does mean this isn't something to deploy publicly without a lot more work.

## Getting it running

You need Python 3.12 (3.9 chokes on the `X | None` syntax used throughout) and a free Groq key from https://console.groq.com/keys.

```bash
python3.12 -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env              # then paste your GROQ_API_KEY in
```

Two processes, two terminals:

```bash
uvicorn backend.api.main:app --reload --reload-dir backend --port 8000
```

```bash
streamlit run frontend/app.py
```

Then open http://localhost:8501.

`--reload-dir backend` matters more than it looks. Plain `--reload` watches the whole project including `storage/repositories/`, so every repo you upload restarts the server and reloads the embedding model. The symptom is that your upload works and the very next click times out, which sends you looking at the frontend where the problem isn't.

## Using it

Zip up a repo, upload it through the sidebar, give it an id. Pick it from the dropdown, describe what's broken, hit start. Takes about 5 to 30 seconds depending on how much retrying the agent does.

You get back a patch, the file it targets, and three validation results. Then you either approve it, reject it, or ask for changes. That last one sends your feedback back to the model and you get a new patch to look at. It's a loop, not a yes/no gate.

`walkthrough.md` has a full session captured verbatim, including the parts a single successful run doesn't prove.

## Tests

```bash
pytest
```

25 tests covering the six tools in isolation. No Groq calls, so it runs in about
15 seconds and fails for real reasons instead of rate limits. Each run builds a
throwaway repository under `storage/repositories/` and deletes it afterwards, so
it won't touch anything you've uploaded.

The one worth knowing about is `test_stale_bytecode_cannot_fake_a_pass`. Python
validates a `.pyc` by source mtime and size, and `a + b` and `a - b` are the same
size, so cached bytecode from a passing run can survive a revert and report
PASSED for code that is no longer on disk. That silently broke the entire test
signal once. The test exists so it can't come back quietly.

## How it works

Seven nodes in a LangGraph state machine:

```
parse -> retrieve -> classify -> generate -> validate -> approve -> finalize
```

Three of the edges are conditional:

- If no patch got generated, skip validation and review entirely.
- If the patch has a syntax error, won't apply, targets a file that doesn't exist, or fails tests, go back to `generate` with the failure in the prompt. Up to 3 attempts.
- If a human asks for changes, go back to `generate` with their feedback. Not capped, because that's a person making a choice each time.

Feeding the failure back is necessary, not decorative. Temperature is 0, so without the error text the model just regenerates the identical broken patch forever.

### Testing is comparative

The suite runs twice, once before the patch and once after:

| Before | After | What it means | What happens |
|---|---|---|---|
| fail | pass | the patch fixed it | send to human |
| pass | pass | the patch broke nothing | send to human |
| pass | fail | the patch broke working tests | retry |
| fail | fail | the patch didn't fix it | retry |

"Tests are red" on its own tells you nothing, because the bug you're reporting is usually itself a failing test. The comparison is what makes the signal mean something.

### Sessions survive

`human_approval` calls LangGraph's `interrupt()`, which serializes the whole session into SQLite and returns. The approval arrives later as a separate HTTP request, possibly after a restart. You can close the tab, come back with the session id, and pick up where you left off.

## API

Six endpoints. Interactive docs at http://localhost:8000/docs.

| Endpoint | What it does |
|---|---|
| `POST /repositories/upload` | zip upload, size and bomb limits, safe extract, index |
| `GET /repositories` | what's uploaded, with chunk counts |
| `POST /debug` | start a session, run until it has a patch to show you |
| `POST /sessions/{id}/approve` | approve, reject, or revise |
| `GET /sessions/{id}` | check state without advancing anything |
| `GET /health` | liveness |

`/debug` and `/approve` return the same shape, so you branch on `status` rather than on which endpoint you called:

```json
{"session_id": "...", "status": "awaiting_approval", "review": {"patch": "...", "test_result": "..."}}
{"session_id": "...", "status": "completed", "final_report": "..."}
```

A revise comes back as `awaiting_approval` with a new patch, which is exactly why both endpoints share one response model.

## The agent's tools

Six of them. Every one takes a `repository_id` and derives its own path, so nothing can write to one directory and validate against another.

`search_codebase` does semantic search over the indexed repo. `analyze_stack_trace` pulls the error type and frames out of a traceback. `inspect_dependencies` reads `requirements.txt` and friends. `run_syntax_check` runs `py_compile`, `run_linter` runs ruff, and `run_unit_tests` runs pytest.

Path handling is strict: absolute paths rejected, `..` blocked with `pathlib.relative_to()`, `.py` files only, everything under the repo root. That stops the agent from naming a file outside the sandbox. It does not stop code inside the repo from doing whatever it wants once pytest imports it, which is the warning above.

## Layout

```
backend/
  __init__.py        loads .env, anchors config paths to the project root
  rag/               loader, language-aware splitter, Chroma store
  tools/             the six agent tools
  graph/             state, nodes, routing, workflow
  api/               schemas and routes
frontend/
  app.py             the Streamlit UI
storage/             uploaded repos and the vector index (gitignored)
walkthrough.md       a real session, captured
```

## Stack

Python 3.12, LangGraph for the state machine, Groq for inference at temperature 0, ChromaDB with local `all-MiniLM-L6-v2` embeddings, FastAPI, Streamlit, ruff and pytest for validation. SQLite for session checkpoints.

I benchmarked the embedding model against `gte-modernbert-base` on this repo and they scored identically at 6/6 top-3 recall, so MiniLM stays. It's a seventh the size. Worth noting that `jina-embeddings-v2-base-code` is broken on transformers 5.x: it random-initializes the encoder and returns noise without raising anything.

## What it doesn't do

One file per patch. Bugs that span several files are outside what the current design expresses.

Test discovery is convention-based. It looks for `tests/test_<name>.py` and a couple of variants. Miss that and you lose the test signal, though syntax and lint still run.

Rejection feedback goes into the final report but doesn't reach the model. Only "request changes" does that.

Retrieval quality on a large repo is untested. Everything I've run it against has been small.

## Config

Everything lives in `.env`, and `.env.example` documents all of it. The ones you'll actually touch:

`GROQ_API_KEY` is required. `MODEL_TEMPERATURE` should stay at 0 unless you also change how retries work. `MAX_AGENT_RETRIES` defaults to 3. `MAX_REPOSITORY_SIZE_MB` defaults to 20 for the upload, with a separate cap at 20x that for the extracted size so a zip bomb can't fill your disk.

## License

MIT.
