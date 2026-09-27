# How Weekend Loop is built

## How it is framed

The system is a work picker plus a contractor, with the judgement kept apart from the side effects:

| Role | Decides | Nature |
|---|---|---|
| Scheduler | when to run and how much to spend | policy; systemd timers, cron as the fallback |
| Triage | what to do | deterministic pre-filter, then one categorical LLM verdict per candidate |
| Worker | how to do it | `claude -p` in a prepared checkout, credential-less, fenced |
| Gate | whether it is deliverable | tests, lint, diff policy and secret scan run by the orchestrator |
| Reporter and web | what the human sees and answers | templates over Pydantic state; a local web application |

Judgements that stay separate:

- **Eligibility** (policy): assignee is nobody or me, no `weekend:never`, body long enough, title
  outside the excluded patterns, no open linked pull request, no recent activity by someone else.
- **Spec signals** (free): the `Business requirement / Goal / Scope` template, paths that resolve in
  the repository, acceptance criteria.
- **Assessment** (LLM, read-only): `execute | propose | needs_input | skip`, effort `XS..L`, risk
  `docs..interface`, blockers, plan, touched paths, up to three questions, confidence.
- **Consent**: `weekend:auto` is pre-consented, the default requires `weekend:approved`,
  `weekend:never` excludes.
- **Budget**: an envelope in list-price dollars as reported by the CLI, a per-task cap, a task count
  equal to the reviewer's Monday capacity, a run duration, and a guard on the weekly reset time.

Ownership split: humans own issue state, assignees, milestones, `main`, merges and CI files; the
agent owns `weekend:*` labels, `weekend/<n>-<slug>` branches, the draft pull requests it opened, its
signed comments and the run state. The orchestrator holds every credential; the worker holds none.

## Layout

```
config.yaml                  the configurable boundaries, budgets, labels, models, repositories, schedule
weekend_loop/models.py      the contract: Assessment, Delivery, Issue, Task, RunState, Policy, ...
weekend_loop/schemas.py     regenerates schemas/*.json handed to the LLM through --json-schema
weekend_loop/policy.py      loads config.yaml and anchors its paths
weekend_loop/records.py     atomic JSON records and append-only ledgers
weekend_loop/cli.py         preflight | candidates | triage | agreement
weekend_loop/preflight.py   the checks a run must clear: binaries, credentials, fences, reset time
weekend_loop/github.py      the only module that talks to GitHub; reads issues, comments, pull requests
weekend_loop/board.py       the local board on disk: issues, comments, pull requests as JSON
weekend_loop/local_board.py the reader and writer over a local board, standing in for GitHub
weekend_loop/backends.py    the reader and writer contracts, and the factory that picks a backend
weekend_loop/prefilter.py   eligibility rules and spec signals, as pure functions
weekend_loop/workbench.py   the checkout the assessor reads, cloned once and reset per run
weekend_loop/claude_cli.py  builds, runs and classifies every `claude -p` invocation
weekend_loop/assessor.py    one read-only verdict per surviving issue, within the envelope
weekend_loop/triage.py      the triage phase: pre-filter, rank, assess, record
weekend_loop/worker.py      one credential-less `claude -p` per approved task, under a timeout
weekend_loop/gate.py        diff policy, secret scan and the repository's own verification commands
weekend_loop/acceptance.py  hidden tests the worker never sees, run against the delivered branch
weekend_loop/execute.py     the execute phase: consent, branch, worker, commit, gate, ledger
weekend_loop/waves.py       groups the approved tasks into waves whose touched paths are disjoint
weekend_loop/publish.py     the publish phase: push, draft pull request, comment, label, digest
weekend_loop/session.py     one unattended weekend: triage, the approved work, the digest
weekend_loop/prepare.py     the mid-week round: triage, the questions on each issue, the alert
weekend_loop/questions.py   the thread contract: hidden markers, the question comment, the reply grammar
weekend_loop/intake.py      reads the operator's replies on the issues into the briefing
weekend_loop/adopt.py       taking up a prepared triage, re-assessing only what changed
weekend_loop/briefing.py    the operator's standing answers and notes, kept across runs
weekend_loop/notify.py      the outbound alert: a webhook the operator points at Slack or Discord
weekend_loop/web/           the web application: the run, its questions, the standing notes and the digest
weekend_loop/lock.py        the lock that keeps two runs off the same state directory
weekend_loop/supervision.py the run's pulse: what it is doing now, its stop requests and its waits
weekend_loop/allowance.py   waiting out the five-hour window between and inside calls
weekend_loop/attempt.py     one task's worker calls: fresh, resumed after a limit or a stall
weekend_loop/transcript.py  turns a call's streamed transcript into short readable lines
weekend_loop/status.py      a run at a glance: liveness, activity, spend, tasks, events, transcript tail
weekend_loop/watch.py       follows a run live: new transcript lines, new events, each change of activity
weekend_loop/schedule.py    renders the crontab from the policy's schedule block
weekend_loop/systemd_units.py renders the systemd services and timers from the same block
weekend_loop/commands.py    running a policy-declared command and keeping the tail of its output
weekend_loop/report.py      renders the triage plan a human reads
weekend_loop/agreement.py   scores a run against the operator's blind labels
weekend_loop/runs.py        the run directory: state, events, per-task records, mailbox
weekend_loop/mailbox.py     the web-to-orchestrator protocol: stop, pause, approve, skip, answer
weekend_loop/workspace.py   finds the operator workspace and refuses a half-built one
weekend_loop/workspace_init.py  creates the workspace: its directories, modes, config and fences
weekend_loop/config_errors.py   turns a rejected config into one line per key, with a suggestion
weekend_loop/config_view.py the configuration as the run sees it, and the reference document
weekend_loop/fences.py      renders both fences, naming the operator home and the workspace
weekend_loop/labels.py      the six `weekend:*` labels and how they reach a board
weekend_loop/resources/     the prompts, schemas, fence templates and starter config the package ships
weekend_loop/demo/          builds, resets and publishes the example board; it never runs during a run
examples/demo-repo/         the example repository (pocketchat), a chat page mockup with unfinished work
examples/issues/            seven seeded issues with the outcome the example expects from each
examples/acceptance/        hidden acceptance tests the orchestrator runs against delivered branches
```

The operator workspace, not the checkout, holds everything a run reads and writes:

```
~/.weekend-loop/config.yaml        the repositories, budgets, labels, identity and schedule
~/.weekend-loop/secrets/           the Claude token, the repository tokens, the alert webhook
~/.weekend-loop/prompts/           prompt overrides, and the conventions of each repository
~/.weekend-loop/acceptance/        hidden tests the worker never sees
~/.weekend-loop/agent-home/        the assistant's own HOME and its two rendered fences
~/.weekend-loop/work/<repo>/       the checkout a run works in
~/.weekend-loop/work/<repo>-worktrees/  one worktree per task while a parallel run works it
~/.weekend-loop/state/             run directories, the board, the ledger, the briefing, the logs
```


## Working in parallel

With `worker.parallel` above 1, the execute phase works several tasks at once. The model:

- One branch per task, `weekend/<n>-<slug>`, on a git worktree of its own under
  `work/<repo>-worktrees/<branch>`, made from `origin/<base>` with the repository's setup commands
  run inside it. The worker, the gate and the hidden acceptance test run in that worktree; the
  shared checkout stays on the base branch, and `publish` pushes from it.
- Waves scheduled by the assessed `touched_paths`. `waves.py` takes the approved tasks in their
  execution order and puts a task into the wave being formed when its paths are disjoint from
  everything the wave already claims; otherwise it starts the next wave. A path covers itself and
  everything under it. A task that touches a `shared_paths` entry, or whose assessment names no
  paths, takes a wave of its own. Two tasks in one wave never change the same hand-written file.
- `worker.shared_paths` for files many tasks append to: a changelog, a generated catalog, a docs
  table, a list of routes. A task that touches one runs alone, so the additions land one after
  another; at merge the operator takes both sides and regenerates what is generated.
- A check after the fact. The gate records the files each branch actually changed; when two tasks
  of one wave changed the same hand-written file, both record the overlap, the digest lists the
  pair under "Merge with care", and the pull request title carries "[merge care]". Publishing goes
  ahead; the reviewer merges such branches one at a time.
- One lock on the run's records. Every write to `run.json`, the events, the ledger and the pulse
  goes through it, and the pulse lists every task in flight.
