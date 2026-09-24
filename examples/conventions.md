# Repository conventions: the example board

Purpose: `pocketchat`, a clickable mockup of the Pocketchat chat assistant. A standard-library HTTP server
serves one chat page and answers questions with prepared replies. It is the pilot target of a weekend run.

Layout: `pocketchat/` package (`answers.py` prepared replies, `chat.py` the conversation, `routes.py`
requests to responses, `server.py` the HTTP server and the `pocketchat` entry point), `pocketchat/static/`
the page (`index.html`, `app.js`, `style.css`), `tests/` pytest suite, `pyproject.toml` (uv
project, Python 3.12, no runtime dependencies).

Gate, run from the repository root with the environment already synced:
- `uv run --no-sync pytest tests --tb=short -q`
- `uv run --no-sync ruff check .`
- `uv run --no-sync ruff format --check .`

Tests exercise `pocketchat.routes.route` directly and open no network port.

Style: full type annotations, frozen dataclasses for records, `Final` constants for defaults, names
spelled out, no docstrings. The README is plain English for readers who are not developers.
Commit subjects: `type(scope): summary`, for example `feat(chat): start a new chat`;
the orchestrator commits with the subject the delivery states.

Forbidden for the agent: `.github/**`, `pyproject.toml`, `uv.lock`, `.pre-commit-config.yaml`, `.env`.
The pre-commit hooks in this repository are intentionally red and must never be run or installed.
