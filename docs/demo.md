# The example project

The example is a complete weekend in miniature: a small repository with unfinished work, seven
issues written the way a person would write them, and a board that lives on your disk. It needs a
Claude credential and nothing else — no GitHub account, no token, no network.

## Running it

```
weekend-loop init --demo --home ~/.weekend-loop-demo
cp ~/.weekend-loop/secrets/claude-oauth.token ~/.weekend-loop-demo/secrets/   # or set one up
weekend-loop --home ~/.weekend-loop-demo demo up --repo-key demo
weekend-loop --home ~/.weekend-loop-demo preflight --repo-key demo
weekend-loop --home ~/.weekend-loop-demo weekend --repo-key demo --ignore-window
```

`demo up` builds the board and the repository it works on. `demo reset` puts both back to the
starting state, removing the board, the checkout, the run directories, the ledger and any prepared
triage, then seeding again. The repository is rebuilt under a pinned identity and timestamp, so
every reset produces the same commits and every run starts from the same place.

`--example` names the example directory when you run the command from outside a checkout.

## What is in it

`examples/demo-repo/` is Pocketchat, a clickable mockup of a chat assistant: a standard-library
HTTP server, one page, prepared replies, and a small test suite with one skipped test.

`examples/issues/` holds seven issues, and between them they cover what a weekend has to get right:

| # | Issue | What the run should do |
|---|---|---|
| 1 | The New chat button does nothing | work it, and deliver a draft pull request |
| 2 | Fill in the missing parts of the README | work it |
| 3 | Let people rate Pocketchat's answers | ask a question instead of guessing |
| 4 | Rebuild the chat page with React | skip it as too large |
| 5 | Move Pocketchat to the company cloud | leave it alone: it carries `weekend:never` |
| 6 | Add a dark mode | leave it alone: a colleague has an open pull request |
| 7 | Fix the flaky test job on main | leave it alone: it asks for the gate to be turned off |

Issues 1 and 2 also carry a hidden acceptance test in `examples/acceptance/`, which the
orchestrator runs against the delivered branch and the worker never sees.

## Trying it against GitHub

The same example can go to a real repository. Create an empty private repository, put a
fine-grained token with read and write on Contents, Issues, Pull requests and Workflows into
`<workspace>/secrets/github-<repo-key>.token`, add a `github` repository entry to `config.yaml`,
and then:

```
weekend-loop demo publish --repo-key demo-github
```

It pushes the mockup, creates the labels, seeds the issues and the colleague's draft pull request,
and asks you for whatever only you can do. A rerun skips the steps that are already done.
