# Working on Weekend Loop

```
uv sync
uv run pytest tests/unit tests/contract     # the gate
uv run pytest tests                         # adds the end-to-end tier
uv run mypy
uv run ruff check . && uv run ruff format --check .
uv run pre-commit run --all-files           # all of the above
uv run python -m weekend_loop.schemas weekend_loop/resources/schemas
```

The conventions this repository holds itself to are in [guidelines.md](../guidelines.md).

## The three test tiers

- `tests/unit/` — fast, no network, no whole runs. Fake `claude`, `gh` and sandbox binaries come
  from `tests/support/fakes.py`; real `git` runs against repositories under `tmp_path`.
- `tests/contract/` — one suite run against both board backends, so the two stay interchangeable.
  The GitHub half runs against a fake `gh`: it pins the call shape and the parsing, not GitHub's
  own behaviour.
- `tests/e2e/` — a whole weekend in a temporary workspace: `init --demo`, `demo up`, `weekend`,
  then assertions on the board, the labels and the digest. It takes seconds and runs in CI.

Nothing in the suite reaches the network, needs a credential, or names a real repository. A guard
test refuses any tracked file carrying an address outside the reserved example domains, a
repository owner outside the examples, or somebody's home directory.
