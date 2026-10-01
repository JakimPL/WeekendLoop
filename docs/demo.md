# The example project

The example is a small weekend run you can try yourself. It has a small repository with unfinished
work, seven issues written the way people write them, and a board on your disk. It runs the real
assistant, so you need a Claude credential. You need no GitHub account and no GitHub token.

## Running it

From a checkout of this repository:

```
weekend-loop demo up
eval "$(weekend-loop demo env)"
weekend-loop weekend --ignore-window
```

- `demo up` builds the example in `~/.weekend-loop-demo`: the workspace, the board, the
  repository and the hidden tests. It asks for your Claude token once and prints one line per step.
  Run it again any time; it keeps what is there.
- `demo env` prints three `export` lines. With `eval`, that terminal's `weekend-loop` and `gh` work
  on the example.
- `demo reset` puts the board, the repository and the run history back to where `demo up` left
  them. The repository is rebuilt with a fixed identity and timestamp, so every reset gives the
  same commits. Your token and settings stay.
- `demo remove` deletes `~/.weekend-loop-demo` after asking once.

Pass `--home` to keep the example somewhere else, and `--example` to name the example directory
when you run these commands outside a checkout. The example keeps to its own workspace and runs
only when you start it.

## The board

The board behaves like a GitHub repository. Weekend Loop reads and writes it through `gh`, the same
way it works on GitHub. For the example, `gh` is a program of Weekend Loop's own that keeps the
issues, comments, labels and pull requests in `~/.weekend-loop-demo/state/board/` and pushes go to
a bare repository next to them. After `demo env`, you use it as you would on GitHub:

```
gh issue list
gh issue comment 3 --body "1. Five stars."
gh issue edit 4 --add-label weekend:approved
gh pr list
gh pr diff 9
```

It answers the commands Weekend Loop and its operator use: `issue list`, `view`, `create`,
`comment`, `edit`, `close` and `reopen`; `pr list`, `view`, `create`, `diff` and `close`;
`label create` and `list`; and the `api` calls a run makes. Any other command says so by name.

## What is in it

`examples/demo-repo/` is Pocketchat, a clickable mockup of a chat assistant. It has a
standard-library HTTP server, one page, prepared replies and a small test suite with one skipped
test.

`examples/issues/` has seven issues. Together they cover what a weekend run has to get right:

| # | Issue | What the run should do |
|---|---|---|
| 1 | The New chat button does nothing | Work on it and deliver a draft pull request |
| 2 | Fill in the missing parts of the README | Work on it |
| 3 | Let people rate Pocketchat's answers | Ask a question instead of guessing |
| 4 | Rebuild the chat page with React | Skip it as too large |
| 5 | Move Pocketchat to the company cloud | Leave it alone, because it has the `weekend:never` label |
| 6 | Add a dark mode | Leave it alone, because an open pull request already links to it (`Closes #6`) |
| 7 | Fix the flaky test job on main | Leave it alone, because it asks to turn off the gate |

Issues 1 and 2 also have a hidden acceptance test in `examples/acceptance/`. The orchestrator runs
it on the delivered branch, and the worker never sees it.

## Trying it on GitHub

You can also run the example against a real repository:

1. Create an empty private repository.
2. Create a fine-grained token with read and write access to Contents, Issues, Pull requests and
   Workflows. Save it as `~/.weekend-loop-demo/secrets/github-demo-github.token`.
3. In `~/.weekend-loop-demo/config.yaml`, set `repos.demo-github.slug` to your repository.
4. Run:

```
weekend-loop demo publish
```

This pushes the mockup and the `feat/dark-mode` branch, creates the labels, and adds the issues
and a draft pull request from `feat/dark-mode` that links to #6, with the same code `demo up` uses
for the board on your disk. It
asks you to do anything only you can do. If you run it again, it skips finished steps.
