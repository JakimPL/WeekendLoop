# Weekend Loop

Most weeks a Claude subscription ends with allowance left over, and most backlogs end the week with
small, well-described jobs nobody got to. Weekend Loop spends the first on the second. It runs on
Friday night without you, picks the issues that are safe to work unattended, hands each one to
Claude Code in a prepared checkout, checks the result against your own tests before it believes it,
and leaves draft pull requests and a digest for Monday morning.

It does nothing quietly. Every issue it worked, skipped or asked about is recorded, and anything it
is unsure of becomes a question on that issue rather than a guess in your repository.

## What it needs

- Linux. The worker runs inside a sandbox built from `bwrap` and `socat`, and the orchestrator
  reads `/proc` to watch it. macOS is not supported.
- A Claude subscription and the `claude` command, signed in with `claude setup-token`.
- `git`, and `gh` when your board is on GitHub.
- Python 3.12 and [uv](https://docs.astral.sh/uv/).

## Install it

```
uv tool install git+https://github.com/JakimPL/WeekendLoop.git
weekend-loop init
```

While the repository is private, git asks for your GitHub username and a personal access token.

`init` creates your workspace at `~/.weekend-loop`: the configuration, a place for secrets, the
assistant's own home, and the state of every run. Point it somewhere else with `--home` or
`WEEKEND_LOOP_HOME`.

Then give it the credential it cannot obtain for you: run `claude setup-token` and save what it
prints to `~/.weekend-loop/secrets/claude-oauth.token`, readable only by you.

## Try it on the example first

The example is a whole weekend in miniature, on a board that lives on your disk. It needs no GitHub
account and no token:

```
weekend-loop init --demo --home ~/.weekend-loop-demo
weekend-loop --home ~/.weekend-loop-demo demo up --repo-key demo
weekend-loop --home ~/.weekend-loop-demo weekend --repo-key demo --ignore-window
```

You get a draft pull request or two, one issue asked a question, one skipped as too large, and
three left alone for reasons the digest explains. [docs/demo.md](docs/demo.md) says what each of
the seven issues is there to prove.

## Point it at your own repository

Describe the repository in `~/.weekend-loop/config.yaml`:

```yaml
repos:
  myrepo:
    slug: your-org/your-repository
    mode: dry_run            # read and report; switch to execute when you trust it
    backend: github
    gate_commands:
      - "pytest -q"

identity:
  git_author_email: you@example.com
```

Everything else takes a default — `weekend-loop config reference` prints every key with the value
it takes when you leave it out. Then:

```
weekend-loop labels --repo-key myrepo        # create the weekend:* labels
weekend-loop preflight --repo-key myrepo     # what is still missing
weekend-loop candidates --repo-key myrepo    # which issues the rules let through
weekend-loop triage --repo-key myrepo        # what it would do, and why
```

Label an issue `weekend:auto` to pre-consent to it, or `weekend:never` to keep it out for good.
When the triage reads right, set `mode: execute` and let a weekend run.

## How a weekend goes

**Thursday** `weekend-loop prepare` reads the backlog and asks its questions as comments on the
issues, so you can answer from your phone.

**Friday night** `weekend-loop weekend` takes up that triage, works the approved issues one at a
time inside its budget, and stops when the queue, the envelope or the weekend is done.

**Monday** the draft pull requests are waiting, each one linked from its issue, with a digest of
what happened and what it cost.

`weekend-loop status` and `weekend-loop watch` show a run in progress; `weekend-loop web` serves the
same view, and the questions, on localhost.

## What it will not do

- It writes only `weekend:*` labels, `weekend/*` branches and draft pull requests. Issue state,
  assignees, milestones, your default branch and merges stay yours.
- The worker holds no credential, cannot reach the network, and cannot run `git` or `gh`.
- A branch is only offered after your own gate commands pass, the diff stays inside its limits, no
  forbidden path is touched and a secret scan comes back clean.
- It stops before your subscription's ceiling, so a weekend run does not eat Monday's allowance.

## Reading further

- [docs/configuration.md](docs/configuration.md) — every key, the labels, the identity, the prompts
- [docs/operating.md](docs/operating.md) — the commands, the budget, systemd and cron, watching a run
- [docs/answering.md](docs/answering.md) — how the agent asks, and how your answers reach it
- [docs/architecture.md](docs/architecture.md) — how it is put together, and where each part lives
- [docs/demo.md](docs/demo.md) — the example project and its seven issues
- [docs/developing.md](docs/developing.md) — running the tests, and the conventions it holds to

## License

MIT. See [LICENSE](LICENSE).
