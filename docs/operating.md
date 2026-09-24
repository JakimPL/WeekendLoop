# Operating a run

## Before the first run

Two things Weekend Loop cannot do for you:

1. `claude setup-token`, saved to `<workspace>/secrets/claude-oauth.token` with mode 0600.
2. `socat` installed next to `bwrap` for the sandbox's network layer (`apt-get download socat` and
   `dpkg -x` into `~/.local` works without root), or an administrator who will.

A repository on the `github` backend needs a fine-grained token with read and write on Contents,
Issues, Pull requests, Workflows and Metadata, in `<workspace>/secrets/github-<repo-key>.token`.

`weekend-loop preflight --repo-key <key>` reports which of these are still outstanding, and every
phase refuses to start until they are in place. The two fences under `<workspace>/agent-home/` are
rendered before each run from the templates the package carries, naming the home directory of
whoever runs it, so the sandbox denies that operator's `.ssh`, `.aws`, `.config/gh` and `.claude`,
and the workspace's own secrets and state.

The allowance needs no recording. The agent reads the subscription's own five-hour and seven-day
figures from the CLI, stops the run before the ceiling, and refuses to start once the account is
drawing on usage credits, so a run spends the subscription and never the card.
`budget.weekly_reset_at` remains available as a stricter override.

The order of a first weekend: `preflight`, then `candidates` and blind-label the sheet, then
`triage` and `agreement` to see whether the judgement matches yours, and only then `weekend`.

## Running a triage

```
weekend-loop preflight  --repo-key demo
weekend-loop candidates --repo-key demo         # writes the blind-label sheet
weekend-loop triage     --repo-key demo         # assesses, writes outbox/plan.md
weekend-loop agreement                           # scores the latest run against the sheet
weekend-loop execute    --repo-key demo         # works the approved tasks on local branches
weekend-loop digest                              # renders the digest without publishing
weekend-loop publish    --repo-key demo         # pushes, opens draft PRs, posts the digest
weekend-loop weekend    --repo-key demo         # all of it, unattended
weekend-loop prepare    --repo-key demo         # the mid-week round that asks first
weekend-loop intake     --repo-key demo         # reads the replies on the issues
weekend-loop web                                 # the operator's web application
weekend-loop status                              # what the latest run is doing right now
weekend-loop watch                               # follows the latest run live
```

## What each phase does

`candidates` lists the issues the deterministic rules let through and writes
`<workspace>/state/dryrun/<repo>-labels.csv`. Fill in `label` with `never` or `not-never`, and optionally an
expected blocker, **before** running `triage`: that is what the agreement metric measures. `triage`
re-runs preflight and stops when it fails, buys one verdict per survivor until the envelope would no
longer cover another one, and writes the plan, the run state, the event log and one
`assessment.json` per assessed issue. It makes no change on GitHub in either mode.

`execute` refuses to run against a dry-run repository. For each approved task it resets the workbench
to the base branch, creates `weekend/<issue>-<slug>`, hands the issue to a worker that holds no
credential and cannot run git, commits whatever the worker left on disk under the agent's own
identity, and then judges the branch itself: the repository's gate commands, the diff policy (size,
forbidden paths, binaries), a secret scan, and the hidden acceptance test when the demo mapping names
one. A task that asks a question instead of guessing ends with a clean tree and no commit. Nothing is
pushed; that is what `publish` does.

Consent comes from the labels a human wrote: `weekend:auto` and `weekend:approved` are worked,
everything else is left alone and the reason is recorded in the event log. The web application can
approve a task for this run without touching the labels.

`publish` is the only command that changes anything on GitHub, and it refuses a dry-run repository.
For each branch the gate accepted it pushes by explicit refspec (`refs/heads/weekend/<n>-<slug>`)
after checking that the workbench remote is the repository the policy names, opens a **draft** pull
request that says `Refs #<issue>` so the issue's state stays the human's, comments the link on the
issue, and writes the label last. A task that ended in a question gets the question as a comment and
the `weekend:needs-input` label instead. When the worker stopped before it finished but its changes
still passed the gate, the branch is offered anyway as a draft titled `[unfinished]`, with a
do-not-merge banner and the `weekend:unfinished` label. Every label write is refused unless the
label sits in the `weekend:` namespace. The run then ends with a digest issue, also written to
`outbox/digest.md`.

## An unattended weekend

`weekend` is the entry point the timers call: preflight, then triage, then the approved work, then
publishing, under one lock so a manual run and a scheduled one cannot collide. The run records why
it stopped taking tasks: the allowance, the envelope, the run duration, or the operator. It
publishes what it finished in every case except an operator stop, and it leaves the digest on disk
either way. `--no-publish` keeps a run entirely local.

Every `claude` call streams into a transcript while it runs: `tasks/<n>/assessment-<k>.jsonl` and
`tasks/<n>/worker-<k>.jsonl` for each issue, `probes/probe-<k>.jsonl` for the allowance readings, all
under the run directory, so `tail -f` shows what the agent is doing. `pulse.json` beside `run.json`
names the process, its last heartbeat and its current activity. A call reads nothing from its input,
and one whose transcript stays silent for `worker.idle_minutes` is ended as stalled. Stopping a
call, or the orchestrator dying, ends everything the call started. Every `git`, `gh` and repository
command reads its input from `/dev/null` too, and `git` and `gh` calls end after ten and two minutes;
a call that runs out of time ends the run, and under systemd the run starts again.

A weekend run lives inside the weekend window in `config.yaml` (`schedule.window`, Friday 18:00 to
Sunday 23:59 by default, in the schedule's timezone). `weekend` starts only inside that window;
`--ignore-window` starts it anyway, for a manual run on a weekday. From the close of the window no
new task starts, while a task already under way finishes.

Four things bound a run. The subscription's own allowance comes first: preflight refuses to start
when the seven-day window has no room for another task, and `usage.seven_day_ceiling` keeps the run
clear of the wall by the margin in `usage.seven_day_reserve`. The rest are the envelope in dollars
(a task starts only if the envelope still covers a whole one), `max_tasks` (the reviewer's Monday
capacity), and the weekend window. `budget.weekly_reset_at`, when set, brings that deadline forward
to the recorded weekly reset. Dollar figures are the CLI's list prices, so on a subscription they
measure effort rather than a bill — the allowance is what actually runs out.

The five-hour window never ends a run on its own. When it fills up, before a task or in the middle
of one, the run waits for its reset and carries on: an interrupted assessment is asked again, and an
interrupted worker resumes its own session, keeping everything it has done and read so far. The run
stops only when the reset falls after its deadline; the task in flight then keeps what passed the
gate as an unfinished draft. A weekly limit, the seven-day ceiling or drawing on usage credits end
the run as before.

A run also survives its own process. Every step lands in `run.json` before and after it runs, so
when the process dies — a crash, an out-of-memory kill, a reboot — the next `weekend` start (systemd's
restart, the next timer, or a manual run) picks the open run up where it stood instead of starting
another. A worker that was mid-task resumes its own session on the same branch; one that already
crashed the run twice keeps what is on disk and the run moves on. Publishing is safe to repeat: a
pull request, comment or digest already on GitHub is reused rather than created twice. A run whose
deadline passed while it was down only publishes what it finished.

The schedule lives in `config.yaml` under `schedule:` — the weekday, hour and minute of each run,
the command it runs (`prepare` or `weekend`), the timezone the timers read them in, and the
repository key.

### Running under systemd

systemd user units are the recommended way to schedule runs. They restart a run that crashed or was
killed, stop every process a run started when the run stops, catch up on a timer the machine slept
through, and send an alert when a run ends badly. Render them straight into your unit directory:

```
weekend-loop systemd --output-dir ~/.config/systemd/user
loginctl enable-linger $USER        # lets the timers fire while you are logged out
systemctl --user daemon-reload
systemctl --user enable --now weekend-loop-prepare-thu-2000.timer \
    weekend-loop-weekend-fri-2100.timer weekend-loop-weekend-sat-1000.timer
```

The command writes `weekend-loop-weekend.service`, `weekend-loop-prepare.service` and one timer
per entry under `schedule:`, then prints these steps with the timer names of your schedule. The
units record your workspace, the `weekend-loop` on your `PATH`, and a `PATH` made of the
directories that hold `claude`, `gh`, `git`, `uv` and the sandbox tools. Run it from the shell you
use for `weekend-loop`, and again whenever the schedule or those locations change.

Starting a run by hand, following it and checking on it:

```
systemctl --user start weekend-loop-weekend.service
journalctl --user -u weekend-loop-weekend -f
systemctl --user status weekend-loop-weekend
systemctl --user list-timers 'weekend-loop-*'
```

What to expect:

- `weekend-loop` exits with 0 when it finishes, 3 when preflight, the run lock or the repository
  mode blocks it, and 1 when it crashes.
- The weekend service restarts 90 seconds after a crash or a kill, up to four times in six hours,
  and the run resumes from disk. A blocked run waits for its next timer.
- A worker that runs out of memory ends alone and the run carries on. Stopping the service ends
  every process the run started.
- The timers are persistent: a moment missed while the machine was off fires at the next boot. A
  timer that fires while the run is going leaves that run in place, which makes Saturday's timer
  the catch-up for Friday's.
- The prepare service runs once per timer.
- Any ending other than a clean finish, a manual stop or a block sends one message through the
  alert webhook, naming how the run ended.
- systemd reads `<workspace>/run.env` as literal `KEY=value` lines, so `$HOME` there stays as
  written. The units set `PATH` and `WEEKEND_LOOP_HOME` themselves, and the file serves plain
  overrides such as `LANG` or `TZ`.

### Running from cron

Cron is the fallback for a host without a systemd user session. `weekend-loop crontab` renders the
schedule into a crontab that calls the installed command against your workspace, each entry under
its own `flock` and appending to a log in the workspace:

```
weekend-loop crontab              # inspect it
weekend-loop crontab | crontab -
tail -f ~/.weekend-loop/<workspace>/state/logs/*.log
```

## Watching a run

A run shows what it is doing while it works. Open a second terminal beside the one running
`weekend` and follow it:

```
weekend-loop watch                      # follows the latest run until it ends
weekend-loop status                     # a snapshot of the latest run
weekend-loop status --lines 50          # the same, with a longer transcript tail
weekend-loop watch --run-id <run-id>    # a specific run
```

`watch` opens with the run's status: whether its process is alive, what it is working on and for
how long, the spend and the allowance, the tasks and the latest events. It then prints each new
line of the agent's transcript as it arrives — its thinking, what it says, every tool call with the
command or file it touches, failed tool calls, allowance readings and the result of each call —
along with every new event and a short header each time the run moves on to another activity. It
ends by itself once the run finishes or its process is gone, and Ctrl-C leaves it at any moment
while the run carries on.

The run page of `weekend-loop web` shows the same picture in its live card: the last 30 transcript
lines and the last 10 events, refreshed every 10 seconds while the run is alive, with a link to the
whole transcript of the current call.

Everything behind these views is plain files under `<workspace>/state/runs/<run-id>/`: `pulse.json` names the
process and its current activity, `events.jsonl` logs each step, and every `claude` call streams its
messages into its own transcript, one JSON message per line, ready for `tail -f`:

```
tail -f <workspace>/state/runs/<run-id>/tasks/<issue>/worker-1.jsonl
```

