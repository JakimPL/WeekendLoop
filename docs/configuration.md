# Configuring Weekend Loop

All settings live in one file, `config.yaml`, in your workspace. Weekend Loop finds the workspace
through the `WEEKEND_LOOP_HOME` variable, the `--home` flag, or `~/.weekend-loop`, in that order.

## The minimum

You need a repository and an email address:

```yaml
repos:
  myrepo:
    slug: your-org/your-repository
    mode: dry_run            # dry_run only reads and reports; execute opens draft pull requests
    backend: github          # github, or local for a board on disk
    gate_commands:           # checks a branch must pass before it is offered
      - "pytest -q"

identity:
  git_author_email: you@example.com

schedule:
  repo_key: myrepo           # optional when you have only one repository
```

Every other key has a default. To see them:

```
weekend-loop config reference    # every key with its default
weekend-loop config --defaults   # keys your workspace leaves at the default
weekend-loop config --resolved   # the final configuration a run will use
```

`mode` and `gate_commands` have no default. `mode` decides whether the tool may change anything, so
you choose it. An empty gate would skip your repository's own checks.

Unknown keys are rejected when the configuration loads:

```
~/.weekend-loop/config.yaml: repos.myrepo.base_brunch: unknown key (did you mean base_branch?)
```

## Backends

Each repository has a `backend`:

- `local` stores issues, comments and pull requests as JSON under `state/board/<slug>/`, next to a
  bare git repository. It needs no GitHub account or token. The example project uses it.
- `github` uses `gh` to read issues and open draft pull requests. It needs a fine-grained token in
  `secrets/github-<repo-key>.token`.

Both backends behave the same. Each repository keeps its own checkout, answers and triage. You can
list the same repository twice, once per backend, to rehearse on disk and then run for real.

## Labels

Weekend Loop writes six labels and reads three of them as consent. They all share one namespace, so
you can rename them with one setting:

```yaml
labels:
  namespace: "bot/"          # bot/auto, bot/approved, bot/never, bot/review, ...
```

A label outside the namespace is rejected when the configuration loads and again when written, on
either backend. Run `weekend-loop labels --repo-key <key>` to create the labels on your repository.

## Identity

The agent commits as `identity.git_author_name` and `identity.git_author_email`, and signs its
comments with `identity.comment_footer`.

On GitHub, comments come from the owner of the token. If that is a bot account, add your own handle:

```yaml
identity:
  git_author_email: bot@example.com
  operator_login: your-github-handle
```

Weekend Loop then treats issues assigned to you as its work, reads your replies as answers, and
ignores the bot's own comments when checking for other people's activity. Preflight shows which
account it treats as the operator, so you can catch a mismatch early.

## Repository conventions

The worker gets your repository's conventions along with each task. Point to a file, or use the
packaged default:

```yaml
repos:
  myrepo:
    conventions_prompt: prompts/conventions-myrepo.md   # relative to the workspace
```

By default, the worker reads the code and follows it: the style of the files it edits, the existing
test framework and the recent commit subjects.

## The worker's limits

`worker.allowed_effort`, `worker.allowed_risk` and `worker.max_diff_lines` say what the worker may
take on: by default the efforts XS and S, the risks docs, tests, refactor and behaviour, and 400
changed lines. The assessor reads the same limits with every issue. An issue within them that
leaves nothing open gets `execute` and is worked under its consent label. One beyond them, or with
a judgement call left open, gets `propose`; a proposal appears in the web application as awaiting
approval and is worked once you approve it. One that would exceed the changed-lines limit is
skipped as too large.

```yaml
worker:
  allowed_effort: [XS, S, M, L]
  allowed_risk: [docs, tests, refactor, behaviour, interface]
  max_diff_lines: 1500
```

## Forbidden paths

`repos.<key>.forbidden_paths` names the files the worker leaves alone. They are held twice: the
worker's fence denies its Edit and Write tools on them, and the gate refuses a branch whose diff
touches one. The default list is the repository's own machinery: `.github/**`, `**/.env` and
`**/.env.*`, `**/pyproject.toml`, `**/uv.lock`, `**/Makefile`, `**/.pre-commit-config.yaml` and
`config/**`. A repository that states its own list replaces the default, so name everything you
want kept and leave out what the worker must be able to change, such as a `config/` folder that
holds authored content:

```yaml
repos:
  myrepo:
    forbidden_paths:
      - ".github/**"
      - "**/.env"
      - "**/.env.*"
      - "pyproject.toml"
      - "uv.lock"
      - "frontend/package.json"
```

A pattern that starts with `**/` matches at the root as well as below it. The rest of the fence
stays whatever the list says: the worker never runs `git`, `gh`, `make`, `pre-commit`, a package
manager or a network tool, and never reads the operator's credentials or the workspace's secrets
and state.

## Working in parallel

Every issue works on its own branch, in a git worktree of its own under `work/<repo>-worktrees/`.
By default a run works one issue at a time; `worker.parallel` lets it work several at once:

```yaml
worker:
  parallel: 2              # how many issues may work at once
  max_stack_depth: 2       # how many issues may build on each other in one run; 0 turns it off
  shared_paths:            # files many issues append to; their additions merge by taking both sides
    - "CHANGELOG.md"
    - "docs/generated/**"
```

Each worker is told the paths the other tasks change, and after the run every pair of branches
that changed the same hand-written file is checked with git: a pair git cannot merge on its own is
marked "[merge care]". `shared_paths` names files that many issues append to, such as a changelog,
a generated catalog or a table in the docs; they stay out of that check, since every issue appends
to them and the reviewer takes both sides. An issue that builds on another one the run also works
starts from that issue's branch, at most `max_stack_depth` issues deep.
[docs/operating.md](operating.md#working-in-parallel) describes how parallel work behaves, what to
do at merge time, and how to write issues that parallelise well.

## Memory

A run shares the machine with you. It gives its tasks a pool of memory and starts a task only
when the pool, and the memory the machine has free, can hold it:

```yaml
resources:
  memory_pool_gb: 32         # all a run's tasks together; half the machine when left out
  memory_reserve_gb: 10      # what the machine keeps free for everything else
  task_memory_gb: 8          # one worker and the tests it runs itself
  gate_memory_gb: 10         # one gate, setup or hidden test command
  gates_at_once: 1           # how many gates run side by side
  cpus_per_task: 6           # each task's processors; pytest -n auto starts as many workers
```

Size `task_memory_gb` and `gate_memory_gb` from what one test run of your repository takes, with
some room on top. The digest's "Memory" section shows the peak of every gate, so the first run
tells you what the numbers should be. `weekend-loop preflight` prints how many tasks the pool fits
at once, and warns when `worker.parallel` asks for more.

With a systemd user session, each worker and each command runs in a scope of its own, capped at
its size. A task that outgrows its cap is stopped alone, and the digest says which command hit
it. Without systemd, the run still waits for free memory before it starts a task, and preflight
says that nothing caps a task that grows past its share. `cpus_per_task` gives each task its own
processors, so test runners that start one worker per processor start fewer.

## Prompt overrides

To replace a built-in prompt, put a file with the same name in `<workspace>/prompts/`:
`worker_system.md`, `assessor_system.md`, `assessor_task.md`, `task_template.md` or
`writing_guide.md`. Prompts you leave alone keep improving with new releases.

`writing_guide.md` is the guide the assessor and the worker both write by: it shapes the plans
you approve, the questions on your issues and the descriptions of the pull requests. It opens
every text with what a person notices, and says outright when a change has nothing to see.
