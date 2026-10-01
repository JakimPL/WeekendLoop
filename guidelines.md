# Guidelines

## Ownership boundaries

- `weekend_loop/github.py` is the only module that talks to a board, and it always does so
  through `gh`. A `local` repository only changes which `gh` runs (`weekend_loop/local_github/`, a
  `gh` that keeps the board on disk) and where `origin` points. Every other module returns data and
  lets the orchestrator decide.
- Phases depend on the `BoardReader` and `BoardWriter` contracts in `weekend_loop/backends.py`.
- `weekend_loop/notify.py` is the only module that reaches an endpoint outside the repository,
  through one webhook URL the operator wrote into `state/`. An alert that fails is a note, never a
  failed run.
- `state/briefing/<repo>/` holds only the operator's own words: what they type into
  `weekend_loop/web/`, and what `weekend_loop/intake.py` transcribes from their replies on an
  issue thread. No phase writes guidance of its own there. `weekend_loop/web/` holds no
  credential and writes only the briefing and the run's mailbox.
- `weekend_loop/questions.py` owns the issue-thread contract: the hidden markers on the agent's
  writing, the question comment and the reply grammar. Asking and reading both go through it.
- The worker (`claude -p`) runs under `agent_home/` and receives no GitHub credential. Anything it
  needs from GitHub arrives pre-rendered in its task file.
- Human-owned state (issue open/closed, assignee, milestone, `main`, merges) is read, never written.
  The agent writes labels in the `weekend:*` namespace only.
- The run directory under `state/` is the source of truth; GitHub labels and comments mirror it.
- `weekend_loop/demo/` writes only the example board and its example repository, and never runs
  during a weekend run; the phases read `state/acceptance.json` and know nothing of the example.
- Configuration is one layer: what the operator writes in `config.yaml` over the defaults the
  models carry. Unknown keys are refused by name, and nothing private enters a tracked file.

## Code

- Run Python through `uv run`.
- Every function signature carries full input and return types, including `None`. Generic types are
  filled (`dict[str, int]`), `Any` appears only at a boundary that genuinely accepts arbitrary data
  (a JSON schema, a raw `gh` payload).
- Validated or serialized data lives in frozen Pydantic models in `weekend_loop/models.py`.
  Models whose schema is handed to the LLM as an output contract forbid extra keys and have no defaults.
- A default value belongs to a named `Final` constant; non-optional inputs are passed explicitly.
- Names are spelled out (`issue_number`, `pull_request_url`).
- Functions with several meaningful steps are split into helpers with one responsibility each; pure
  functions are separated from subprocess and file I/O.
- `__init__.py` files stay empty; callers import from the concrete submodule.
- Failures crash unless the code can recover meaningfully; broad `except` clauses are absent, and a
  best-effort catch binds the exception and logs its reason.
- No docstrings. Names and types carry the meaning; effort goes into those instead.
- Comments mark tensor shapes, third-party quirks and invariants only. Modules carry no docstring.

## Verification

- `uv run pytest tests/unit tests/contract` is the gate, run through `pre-commit` after each
  phase; `uv run pytest tests` adds the end-to-end tier, which runs a whole weekend against the
  fake assistant in a temporary workspace.
- `mypy --strict` and `ruff` pass on `weekend_loop/`, `tests/` and `examples/acceptance/`.
- The JSON schemas the package carries are regenerated from the models
  (`uv run python -m weekend_loop.schemas weekend_loop/resources/schemas`) and a unit test guards
  the two against drift.
