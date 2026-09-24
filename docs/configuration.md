# Configuring Weekend Loop

Everything a run obeys lives in one file, `config.yaml`, inside your workspace. Weekend Loop reads
it through `WEEKEND_LOOP_HOME`, the `--home` flag, or `~/.weekend-loop` when you name neither.

## What you have to write

A repository and an email address:

```yaml
repos:
  myrepo:
    slug: your-org/your-repository
    mode: dry_run            # dry_run reads and reports; execute opens draft pull requests
    backend: github          # github, or local for a board on disk
    gate_commands:           # what has to pass before a branch is offered
      - "pytest -q"

identity:
  git_author_email: you@example.com

schedule:
  repo_key: myrepo           # omit it when you have one repository
```

Every other key takes a default. Two commands show you which:

```
weekend-loop config reference    # every key with the value it takes when you leave it out
weekend-loop config --defaults   # the keys this workspace is leaving to the default
weekend-loop config --resolved   # the configuration the run will actually obey
```

`mode` and `gate_commands` have no default on purpose. `mode` is a consent decision, and an empty
gate would quietly disable your repository's own verification.

A key Weekend Loop does not read is refused by name when the configuration loads:

```
~/.weekend-loop/config.yaml: repos.myrepo.base_brunch: unknown key (did you mean base_branch?)
```

## Where a repository lives

Each repository names a `backend`:

- `local` keeps the issues, comments and pull requests as JSON under `state/board/<slug>/`, and the
  repository itself as a bare git repository beside them. It needs no GitHub account and no token.
  This is what the example project uses.
- `github` is the real thing: `gh` reads the issues and opens the draft pull requests, against the
  fine-grained token in `secrets/github-<repo-key>.token`.

Every phase behaves the same either way, and each repository keeps its own checkout, answers and
prepared triage. Naming the same repository twice, once on each backend, lets you rehearse on disk
and then run the same work for real.

## The labels

Weekend Loop writes six labels and reads three of them for consent. They are named after one
namespace, so renaming them is one line:

```yaml
labels:
  namespace: "bot/"          # bot/auto, bot/approved, bot/never, bot/review, ...
```

A label outside the namespace is refused when the configuration loads, and again at the moment of
writing, on either backend. `weekend-loop labels --repo-key <key>` creates them on your repository.

## Who the operator is

The agent commits under `identity.git_author_name` and `identity.git_author_email`, and signs its
comments with `identity.comment_footer`.

On GitHub the comments belong to whoever owns the token. When that is a bot account rather than
you, name yourself:

```yaml
identity:
  git_author_email: bot@example.com
  operator_login: your-github-handle
```

Weekend Loop then treats your assignment as "mine", reads your replies as the answers, and stops
counting the bot's own comments as somebody else's activity. Preflight reports which account it is
treating as the operator, so a mismatch shows up before it eats a weekend.

## The conventions of a repository

The worker receives your repository's conventions along with its task. Point at a file, or let the
packaged default stand in:

```yaml
repos:
  myrepo:
    conventions_prompt: prompts/conventions-myrepo.md   # relative to the workspace
```

Without one, the worker is told to read the code and follow what it already does: the style of the
files it touches, the test framework that is already there, and the recent commit subjects.

## Prompt overrides

A file in `<workspace>/prompts/` named after one of the prompts the package carries replaces it:
`worker_system.md`, `assessor_system.md`, `assessor_task.md`, `task_template.md`. Anything you do
not override keeps improving with the tool.
