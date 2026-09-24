# The example project

The example is a small weekend run you can try yourself. It has a small repository with unfinished
work, seven issues written the way people write them, and a board on your disk. You need a Claude
credential. You do not need a GitHub account, a token or a network connection.

## Running it

```
weekend-loop init --demo --home ~/.weekend-loop-demo
cp ~/.weekend-loop/secrets/claude-oauth.token ~/.weekend-loop-demo/secrets/   # or set one up
weekend-loop --home ~/.weekend-loop-demo demo up --repo-key demo
weekend-loop --home ~/.weekend-loop-demo preflight --repo-key demo
weekend-loop --home ~/.weekend-loop-demo weekend --repo-key demo --ignore-window
```

`demo up` builds the board and the repository. `demo reset` deletes the board, checkout, run
directories, ledger and prepared triage, then builds everything again. The repository is rebuilt
with a fixed identity and timestamp, so every reset gives the same commits.

Pass `--example` to name the example directory when you run these commands outside a checkout.

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
| 6 | Add a dark mode | Leave it alone, because a colleague has an open pull request |
| 7 | Fix the flaky test job on main | Leave it alone, because it asks to turn off the gate |

Issues 1 and 2 also have a hidden acceptance test in `examples/acceptance/`. The orchestrator runs
it on the delivered branch, and the worker never sees it.

## Trying it on GitHub

You can also run the example against a real repository:

1. Create an empty private repository.
2. Create a fine-grained token with read and write access to Contents, Issues, Pull requests and
   Workflows. Save it as `<workspace>/secrets/github-<repo-key>.token`.
3. Add a `github` repository entry to `config.yaml`.
4. Run:

```
weekend-loop demo publish --repo-key demo-github
```

This pushes the mockup, creates the labels, and adds the issues and the colleague's draft pull
request. It asks you to do anything only you can do. If you run it again, it skips finished steps.
