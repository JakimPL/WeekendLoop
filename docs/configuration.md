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

## Prompt overrides

To replace a built-in prompt, put a file with the same name in `<workspace>/prompts/`:
`worker_system.md`, `assessor_system.md`, `assessor_task.md` or `task_template.md`. Prompts you
leave alone keep improving with new releases.
