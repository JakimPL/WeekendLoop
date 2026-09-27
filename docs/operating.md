# Operating a run

## Before the first run

You need to set up two things yourself:

1. Run `claude setup-token` and save the one line it prints after "Your OAuth token" to
   `<workspace>/secrets/claude-oauth.token` with mode 0600. The command is interactive, so
   redirecting its output saves the whole dialogue, which preflight refuses.
2. Install `socat` next to `bwrap`. The sandbox needs it for networking. Without root, run
   `apt-get download socat` and unpack it with `dpkg -x` into `~/.local`. Or ask an administrator.
3. On Ubuntu 24.04 and later, let `bwrap` create user namespaces. The kernel setting
   `kernel.apparmor_restrict_unprivileged_userns` keeps a process without a profile from doing so,
   and preflight then warns "sandbox namespace: bwrap: loopback: Failed RTM_NEWADDR". A profile
   that allows them, loaded once, settles it:

   ```
   sudo tee /etc/apparmor.d/bwrap >/dev/null <<'EOF'
   abi <abi/4.0>,
   include <tunables/global>
   profile bwrap /usr/bin/bwrap flags=(unconfined) {
     userns,
     include if exists <local/bwrap>
   }
   EOF
   sudo apparmor_parser -r /etc/apparmor.d/bwrap
   ```

A repository on the `github` backend also needs a fine-grained token with read and write access to
Contents, Issues, Pull requests, Workflows and Metadata. Save it as
`<workspace>/secrets/github-<repo-key>.token`. While the repository stays in `dry_run`, keep
Contents at read: preflight refuses a token that can push, so a dry run is unable to change
anything, and raise it to read and write when you switch to `execute`. Preflight learns what the
token can do by attempting a write that cannot succeed, a branch at a commit that does not exist,
and reading the answer: a refusal means read-only, a validation error means the token can push.
The repository's `permissions` field would only describe the account, which for an owner always
reads as push.

`weekend-loop preflight --repo-key <key>` lists what is still missing. No phase starts until
everything is in place.

Before each run, Weekend Loop renders two sandbox fences under `<workspace>/agent-home/` from
packaged templates. They use the home directory of the person running it. The sandbox blocks that
person's `.ssh`, `.aws`, `.config/gh` and `.claude`, plus the workspace's secrets and state.

Usage limits need no setup. The agent reads your subscription's five-hour and seven-day usage from
the CLI and stops the run before the limit. It also refuses to start when the account is using
credits, so a run uses your subscription and never your card. To set a stricter deadline, use
`budget.weekly_reset_at`.

For a first weekend, go in this order:

1. `preflight`
2. `candidates`, then label the sheet without looking at the agent's verdicts
3. `triage` and `agreement`, to see whether the agent's judgment matches yours
4. `weekend`

## Commands

```
weekend-loop preflight  --repo-key demo
weekend-loop candidates --repo-key demo         # writes the labeling sheet
weekend-loop triage     --repo-key demo         # assesses issues, writes outbox/plan.md
weekend-loop agreement                           # scores the latest run against your labels
weekend-loop execute    --repo-key demo         # works approved tasks on local branches
weekend-loop digest                              # renders the digest without publishing
weekend-loop publish    --repo-key demo         # pushes, opens draft PRs, posts the digest
weekend-loop weekend    --repo-key demo         # runs all of it unattended
weekend-loop prepare    --repo-key demo         # mid-week round that asks questions first
weekend-loop intake     --repo-key demo         # reads replies on the issues
weekend-loop web                                 # the operator's web application
weekend-loop status                              # what the latest run is doing now
weekend-loop watch                               # follows the latest run live
```

## What each phase does

**`candidates`** lists the issues that pass the fixed rules and writes
`<workspace>/state/dryrun/<repo>-labels.csv`. Fill in the `label` column with `never` or
`not-never`, and optionally an expected blocker, **before** you run `triage`. The agreement score
compares the agent's verdicts with these labels.

**`triage`** runs preflight again and stops if it fails. It then assesses each remaining issue until
the budget cannot cover another one. It writes the plan, the run state, the event log and one
`assessment.json` per issue. It changes nothing on GitHub, in either mode.

**`execute`** refuses to run on a `dry_run` repository. For each approved task it:

1. Resets the workbench to the base branch and creates `weekend/<issue>-<slug>`.
2. Gives the issue to a worker that has no credentials and cannot run git.
3. Commits whatever the worker left on disk, under the agent's identity.
4. Checks the branch: the repository's gate commands, the diff policy (size, forbidden paths,
   binaries), a secret scan and, for the demo, the hidden acceptance test.

If the worker asks a question instead of guessing, the tree stays clean and nothing is committed.
`execute` pushes nothing. That is `publish`'s job.

With `worker.parallel` above 1, `execute` works several tasks at once, each on its own branch in a
worktree of its own. [Working in parallel](#working-in-parallel) below describes how.

**Consent** comes from labels a person wrote. Issues with `weekend:auto` or `weekend:approved` get
worked. All others are left alone, and the reason goes in the event log. In the web application you
can approve a task for one run without changing labels.

**`publish`** is the only command that changes anything on GitHub. It refuses a `dry_run`
repository. For each branch that passed the gate, it:

1. Checks that the workbench remote is the repository named in the policy.
2. Pushes with an explicit refspec (`refs/heads/weekend/<n>-<slug>`).
3. Opens a **draft** pull request that says `Refs #<issue>`, so people still decide when the issue
   closes.
4. Comments the link on the issue.
5. Writes the label last.

If a task ended in a question, `publish` posts the question as a comment and adds the
`weekend:needs-input` label. If the worker stopped early but its changes passed the gate, the branch
is still offered as a draft titled `[unfinished]`, with a do-not-merge banner and the
`weekend:unfinished` label. Labels outside the `weekend:` namespace are rejected on write. The run
ends with a digest issue, also saved to `outbox/digest.md`.

## An unattended weekend

`weekend` is the command the timers call. It runs preflight, triage, the approved work and
publishing, all under one lock, so a manual run and a scheduled run cannot overlap.

The run records why it stopped taking tasks: the usage limit, the budget, the run time or the
operator. It publishes what it finished in every case except an operator stop. It always leaves the
digest on disk. Use `--no-publish` to keep a run fully local.

### Transcripts and timeouts

Every `claude` call streams into a transcript while it runs, so you can follow it with `tail -f`.
Transcripts are in the run directory:

- `tasks/<n>/assessment-<k>.jsonl` and `tasks/<n>/worker-<k>.jsonl` for each issue
- `probes/probe-<k>.jsonl` for usage readings

`pulse.json`, next to `run.json`, shows the process, its last heartbeat and its current activity.

A call that stays silent for `worker.idle_minutes` is ended as stalled. Stopping a call, or losing
the orchestrator, ends every process the call started. `git` and `gh` calls end after ten and two
minutes. If a call times out, the run ends, and under systemd it starts again. Every `git`, `gh`
and repository command, and every `claude` call, reads its input from `/dev/null`.

### The weekend window

A run stays inside the weekend window in `config.yaml`. By default that is Friday 18:00 to Sunday
23:59 in the schedule's timezone (`schedule.window`). `weekend` starts only inside the window. Use
`--ignore-window` to start it anyway, for example on a weekday. When the window closes, no new task
or wave starts, and the tasks already running finish.

### Limits

Four things limit a run:

- **Subscription usage.** Preflight refuses to start if the seven-day window has no room for
  another task. `usage.seven_day_ceiling` keeps the run below the limit by the margin in
  `usage.seven_day_reserve`.
- **The dollar budget.** A task starts only if the budget still covers a whole task. In a
  parallel run, a wave starts only when the budget covers every task in it.
- **`max_tasks`.** The number of tasks the reviewer can handle on Monday.
- **The weekend window.**

If `budget.weekly_reset_at` is set, it moves the deadline up to the weekly reset. Dollar figures are
the CLI's list prices. On a subscription they measure effort, not a bill. Your usage limit is what
actually runs out.

The five-hour window never ends a run by itself. When it fills up, before a task or during one, the
run waits for the reset and carries on. An interrupted assessment is asked again. An interrupted
worker resumes its own session and keeps everything it has done and read. The run stops only when
the reset falls after its deadline. The task in progress then keeps whatever passed the gate as an
unfinished draft. The weekly limit, the seven-day ceiling and credit use still end the run.

### Crash recovery

A run survives its own process. Every step is saved to `run.json` before and after it runs. If the
process dies (crash, out-of-memory kill, reboot), the next `weekend` start picks up the open run
instead of starting a new one. That start can be systemd's restart, the next timer or a manual run.

- A worker that was mid-task resumes its session on the same branch, in its own worktree when
  the run was parallel. Every interrupted task of a parallel run is taken up, one after another.
- A worker that has already crashed the run twice keeps what is on disk, and the run moves on.
- Publishing is safe to repeat. A pull request, comment or digest already on GitHub is reused.
- A run whose deadline passed while it was down publishes only what it finished.

### The schedule

The schedule is under `schedule:` in `config.yaml`. It sets the weekday, hour and minute of each
run, the command to run (`prepare` or `weekend`), the timezone and the repository key.

## Working in parallel

`worker.parallel` sets how many tasks a run works at once. Above 1, `execute` takes the approved
tasks in their usual order and groups them into waves: a task joins the wave being formed when the
paths its assessment names are disjoint from everything the wave already touches, and otherwise
starts the next wave. A task that touches one of `worker.shared_paths`, or whose assessment names
no paths, takes a wave of its own. The rule the waves keep is that two tasks in one wave never
change the same hand-written file, and the assessor's `touched_paths` is what they keep it with.

Each task in a wave works on its own branch in a git worktree of its own under
`<workspace>/work/<repo>-worktrees/<branch>`, made from the base branch with the setup commands run
inside it; the worker, the gate and the hidden acceptance test all run there. The worktrees go when
the wave ends, and the branches stay for `publish`. A wave holds at most `worker.parallel` tasks
and starts only when the dollar budget covers every task in it; what the budget leaves out waits
for the next wave. `max_tasks` counts across waves, the allowance is probed once before each wave,
and the weekend window is checked between waves. Every task records the wave it ran in, and the
reason when a rule made it run alone; the digest lists the waves.

After a wave, the run compares what each branch actually changed. When two tasks of one wave
changed the same hand-written file, both record it, the digest lists the pair under "Merge with
care", and their pull requests carry "[merge care]" in the title and name each other in the body.
They are still published; merge them one at a time and run the tests after each.

At merge time, take every branch of a wave: their hand-written changes are disjoint by
construction. For a shared file such as a generated catalog, a docs table or a list of routes,
take both sides and regenerate what is generated. For a "[merge care]" pair, merge one, run the
tests, then merge the other.

### Writing issues that parallelise well

- Name the files the work will change, tests and docs included. The assessor turns them into
  `touched_paths`, and the waves come from that list.
- Keep the issues of one batch disjoint. Two issues that change the same file take turns, and a
  batch of issues on one file runs one at a time.
- Name the registries the change appends to, such as a changelog, a generated index or a table in
  the docs, and list them under `worker.shared_paths`, so an issue that touches one runs alone.
- State acceptance as checkbox lines. The assessor reads them as the goal, and the reviewer checks
  the branch against them.

## Running under systemd

systemd user units are the recommended way to schedule runs. They restart a run that crashed or was
killed, stop every process a run started when the run stops, catch up on a timer the machine
slept through, and send an alert when a run ends badly. To install them:

```
weekend-loop systemd --output-dir ~/.config/systemd/user
loginctl enable-linger $USER        # lets the timers fire while you are logged out
systemctl --user daemon-reload
systemctl --user enable --now weekend-loop-prepare-thu-2000.timer \
    weekend-loop-weekend-fri-2100.timer weekend-loop-weekend-sat-1000.timer
```

The command writes `weekend-loop-weekend.service`, `weekend-loop-prepare.service` and one timer per
entry under `schedule:`. It then prints these steps with the timer names from your schedule.

The units record your workspace, the `weekend-loop` on your `PATH`, and a `PATH` built from the
directories holding `claude`, `gh`, `git`, `uv` and the sandbox tools. Run the command from the
shell you normally use for `weekend-loop`. Run it again whenever your schedule or those locations
change.

To start a run by hand, follow it and check on it:

```
systemctl --user start weekend-loop-weekend.service
journalctl --user -u weekend-loop-weekend -f
systemctl --user status weekend-loop-weekend
systemctl --user list-timers 'weekend-loop-*'
```

What to expect:

- `weekend-loop` exits with 0 when it finishes, 3 when preflight, the run lock or the repository
  mode blocks it, and 1 when it crashes.
- After a crash or kill, the weekend service restarts in 90 seconds, up to four times in six hours,
  and the run resumes from disk. A blocked run waits for its next timer.
- A worker that runs out of memory ends alone, and the run carries on. Stopping the service ends
  every process the run started.
- Timers are persistent. A timer missed while the machine was off fires at the next boot. If a
  timer fires while a run is going, the run continues, so Saturday's timer catches up for
  Friday's.
- The prepare service runs once per timer.
- Any ending except a clean finish, a manual stop or a block sends one message to the alert
  webhook that says how the run ended.
- systemd reads `<workspace>/run.env` as literal `KEY=value` lines, so `$HOME` stays as written
  there. The units set `PATH` and `WEEKEND_LOOP_HOME` themselves. Use the file for plain overrides
  such as `LANG` or `TZ`.

## Running from cron

Use cron if your host has no systemd user session. `weekend-loop crontab` turns the schedule into
a crontab. Each entry calls the installed command on your workspace, runs under its own `flock`
and appends to a log in the workspace:

```
weekend-loop crontab              # inspect it
weekend-loop crontab | crontab -
tail -f ~/.weekend-loop/<workspace>/state/logs/*.log
```

## Watching a run

Open a second terminal next to the one running `weekend`:

```
weekend-loop watch                      # follows the latest run until it ends
weekend-loop status                     # a snapshot of the latest run
weekend-loop status --lines 50          # the same, with a longer transcript tail
weekend-loop watch --run-id <run-id>    # a specific run
```

`watch` starts with the run's status: whether the process is alive, what it works on and for how
long, spending and usage, the tasks and the latest events. It then prints each new line of the
agent's transcript as it arrives:

- its thinking and what it says
- every tool call, with the command or file it touches
- failed tool calls
- usage readings
- the result of each call

It also prints new events, with a short header each time the run moves to another activity. It ends
by itself when the run finishes or its process is gone. Press Ctrl-C to leave at any time. The run
carries on.

In a parallel run, `status` lists every task in flight: the `now:` line names the call that
started most recently, and one `also:` line follows for each other task being worked. `watch`
follows the transcript of that most recent call and prints a header when it changes. To follow one
task from start to finish, tail its own transcript:

```
tail -f <workspace>/state/runs/<run-id>/tasks/<issue>/worker-1.jsonl
```

The run page of `weekend-loop web` shows the same in its live card: the last 30 transcript lines
and the last 10 events, refreshed every 10 seconds while the run is alive. It links to the full
transcript of the current call.

For showing a run to other people, open http://127.0.0.1:8788/demo. It is one screen with two
columns: on the left, the run's steps as a table in plain words (time, issue, step, outcome); on
the right, every issue with its current status and a link to its pull request once one exists. It
shows no spend, refreshes every five seconds, and follows the newest run, so it can be open before
`weekend` starts and switches to the new run as soon as it begins. `/demo?run_id=<run-id>` pins one
run.

All of these views read plain files under `<workspace>/state/runs/<run-id>/`. `pulse.json` shows the
process and its current activity. `events.jsonl` logs each step. Each `claude` call streams into its
own transcript, one JSON message per line:

```
tail -f <workspace>/state/runs/<run-id>/tasks/<issue>/worker-1.jsonl
```
