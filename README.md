# Weekend Loop

Most weeks a Claude subscription ends with unused allowance, and most backlogs end the week with
small, well-described jobs nobody got to. Weekend Loop puts the first to work on the second. It runs
on Friday night without you and does this:

1. Picks the issues that are safe to work on unattended.
2. Hands each one to Claude Code in a prepared checkout.
3. Checks the result against your own tests.
4. Leaves draft pull requests and a digest for Monday morning.

Everything is recorded: every issue it worked, skipped or asked about. When it is unsure, it asks a
question on the issue instead of guessing in your repository.

## What it needs

- Linux with a systemd user session, as on desktop Ubuntu. The worker runs in a sandbox built from
  `bwrap` and `socat`, and the orchestrator reads `/proc` to watch it. macOS is not supported.
- A Claude subscription and the `claude` command.
- `git`, and `gh` if your board is on GitHub.
- Python 3.12 and [uv](https://docs.astral.sh/uv/).

## Install

```
uv tool install git+https://github.com/JakimPL/WeekendLoop.git
```

While the repository is private, git asks for your GitHub username and a personal access token.

## Try the example first

The example is a small weekend run on a board stored on your disk. You need no GitHub account:

```
weekend-loop init --demo --home ~/.weekend-loop-demo
weekend-loop --home ~/.weekend-loop-demo demo up --repo-key demo
weekend-loop --home ~/.weekend-loop-demo setup --no-schedule
weekend-loop --home ~/.weekend-loop-demo weekend --repo-key demo --ignore-window
```

`setup` asks for your Claude token: run `claude setup-token` in another terminal and paste the line
it prints after "Your OAuth token". You get a draft pull request or two, one issue with a question,
one issue skipped as too large, and three issues left alone for reasons the digest explains.
[docs/demo.md](docs/demo.md) describes what each of the seven issues tests.

## Set it up for your repository

1. Run `weekend-loop setup`. The first time, it creates your workspace at `~/.weekend-loop` and
   stops.
2. Describe the repository in `~/.weekend-loop/config.yaml`:

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

   Every other key has a default. `weekend-loop config reference` prints them all.
3. Have two tokens at hand:
   - **Claude:** `claude setup-token` signs you in and prints the token after "Your OAuth token".
   - **GitHub:** a fine-grained token for the repository (Settings → Developer settings →
     Fine-grained tokens), with Contents, Issues, Pull requests, Workflows and Metadata. Keep
     Contents read-only while `mode` is `dry_run`.
4. Run `weekend-loop setup` again. It asks once, then saves the tokens, creates the `weekend:*`
   labels, schedules the runs and checks everything, one line per step. Anything that needs root
   comes as a few commands for you to run once.

Run `weekend-loop setup` again whenever you change `config.yaml`; it keeps what is already in place.

## See what it would do

```
weekend-loop candidates --repo-key myrepo    # list issues that pass the rules
weekend-loop triage --repo-key myrepo        # show what it would do, and why
```

Label an issue `weekend:auto` to approve it in advance, or `weekend:never` to exclude it for good.
When the triage looks right, set `mode: execute`, give the GitHub token write access to Contents,
and run `weekend-loop setup` once more.

## How a weekend goes

**Thursday.** The scheduled `weekend-loop prepare` reads the backlog and asks its questions as
comments on the issues, so you can answer from your phone.

**Friday night.** The scheduled `weekend-loop weekend` uses that triage and works the approved
issues within its budget, one at a time unless you let it run several at once. It stops when the queue is empty, the
budget is spent or the weekend ends.

**Monday.** The draft pull requests are waiting, each linked from its issue, with a digest of what
happened and what it cost.

`weekend-loop schedule` shows when the next runs start, and `weekend-loop schedule off` and
`weekend-loop schedule on` pause and resume them. `weekend-loop status` and `weekend-loop watch` show
a run in progress, and `weekend-loop web` shows the same view, plus the questions, on localhost. Its
`/demo` page is a plain two-column view of the run for showing to other people.

## Safeguards

- It writes only `weekend:*` labels, `weekend/*` branches and draft pull requests. You keep control
  of issue state, assignees, milestones, your default branch and merges.
- The worker runs in a sandbox with no credentials, no network access and no `git` or `gh`.
- A branch is offered only when your gate commands pass, the diff is within its limits, no
  forbidden path is touched and a secret scan is clean.
- It stops before your subscription's limit, so a weekend run leaves Monday's allowance intact.

## Further reading

- [docs/configuration.md](docs/configuration.md): keys, labels, identity and prompts
- [docs/operating.md](docs/operating.md): what setup does, commands, limits, scheduled runs
  and watching a run
- [docs/answering.md](docs/answering.md): how the agent asks and how your answers reach it
- [the writing guide](weekend_loop/resources/prompts/writing_guide.md): how to write an issue, a
  plan and a pull request description a busy reader understands; the agent writes by it too
- [docs/architecture.md](docs/architecture.md): how it is built and where each part lives
- [docs/demo.md](docs/demo.md): the example project and its seven issues
- [docs/developing.md](docs/developing.md): running the tests and the project's conventions

## License

MIT. See [LICENSE](LICENSE).
