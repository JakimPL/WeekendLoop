# Working on Weekend Loop

```
uv sync
uv run pytest tests/unit tests/contract     # the gate
uv run pytest tests                         # adds the end-to-end tests
uv run mypy
uv run ruff check . && uv run ruff format --check .
uv run pre-commit run --all-files           # all of the above
uv run python -m weekend_loop.schemas weekend_loop/resources/schemas
```

The project's coding rules are in [guidelines.md](../guidelines.md).

## Test tiers

- `tests/unit/` runs fast, with no network and no full runs. Fake `claude`, `gh` and sandbox
  binaries come from `tests/support/fakes.py`; the fake `gh` pins call shapes and stands in for
  failures, and its payloads come from the local `gh`'s own shapes. Real `git` runs on
  repositories under `tmp_path`.
- `tests/contract/` runs the board contract through the local `gh` and through the fake one.
  `test_live_shapes.py` compares the local `gh`'s answers with GitHub's when
  `WEEKEND_LOOP_LIVE_REPO` names a public repository and `gh` is signed in; otherwise it skips.
- `tests/e2e/` runs a whole weekend in a temporary workspace: `init --demo`, `demo up`, `weekend`,
  then checks the board, the labels and the digest. It takes a few seconds and runs in CI.

Tests stay offline, use no credentials and name no real repository. A guard test fails if a tracked
file contains an email address outside the reserved example domains, a repository owner outside the
examples, or a personal home directory.
