from typing import Final

HEADER: Final[str] = "Setting up Weekend Loop for {slug}"
CONFIRM: Final[str] = (
    "Setup saves the tokens still missing, creates the weekend labels on {slug} and {schedule}. "
    "Go ahead? [y/N] "
)
CONFIRM_SCHEDULE: Final[str] = "schedules runs for {moments}"
CONFIRM_NO_SCHEDULE: Final[str] = "leaves the schedule as it is"
DECLINED: Final[str] = "Nothing changed."
NEEDS_TERMINAL: Final[str] = (
    "Setup asks before it changes anything. Run it in a terminal, or pass --yes."
)

WORKSPACE: Final[str] = "workspace"
WORKSPACE_CREATED: Final[str] = "created at {root}"
WORKSPACE_FOUND: Final[str] = "{root}"
DESCRIBE_REPOSITORY: Final[str] = "describe your repository in {config}, then run setup again"

CLAUDE_TOKEN: Final[str] = "Claude token"
CLAUDE_TOKEN_PROMPT: Final[str] = (
    'Paste the line `claude setup-token` prints after "Your OAuth token": '
)
GITHUB_TOKEN: Final[str] = "GitHub token"
GITHUB_TOKEN_PROMPT: Final[str] = (
    "Paste a fine-grained token for {slug} (Contents, Issues, Pull requests, Workflows, Metadata): "
)
TOKEN_SAVED: Final[str] = "saved"
TOKEN_KEPT: Final[str] = "already saved"
TOKEN_MISSING: Final[str] = "missing; save it to {path}"
TOKEN_CAN_PUSH: Final[str] = "can push to {slug}"
TOKEN_CAN_READ: Final[str] = "can read {slug}"

LABELS: Final[str] = "labels"
LABELS_DONE: Final[str] = "{count} weekend labels on {slug}"
LABELS_REFUSED: Final[str] = "GitHub refused {label}: {reason}"
LABELS_WAIT: Final[str] = "wait for a working GitHub token"

SANDBOX: Final[str] = "sandbox"
SANDBOX_READY: Final[str] = "ready"
SANDBOX_NEEDS_ROOT: Final[str] = "needs the commands below"
SOCKET_FILTER: Final[str] = "socket filter"
SOCKET_FILTER_READY: Final[str] = "installed"
SOCKET_FILTER_INSTALLED: Final[str] = "installed now"
SOCKET_FILTER_NEEDS_NPM: Final[str] = "needs npm; install Node.js, then run setup again"
SOCKET_FILTER_FAILED: Final[str] = "npm could not install it: {reason}"

SCHEDULE: Final[str] = "schedule"
SCHEDULE_ON: Final[str] = "on; next run {next}"
SCHEDULE_ON_WHILE_LOGGED_IN: Final[str] = (
    "on, but runs start only while you are logged in: {reason}"
)
SCHEDULE_LEFT: Final[str] = "left as it is"
SCHEDULE_NO_SYSTEMD: Final[str] = (
    "no systemd user session here; schedule with `weekend-loop crontab | crontab -` instead"
)
SCHEDULE_MISSING_BINARIES: Final[str] = (
    "{names} not on PATH; run setup from the shell that runs weekend-loop"
)
SCHEDULE_FAILED: Final[str] = "{command} failed: {reason}"

PREFLIGHT: Final[str] = "preflight"
PREFLIGHT_CLEAR: Final[str] = "clear to run"
PREFLIGHT_BLOCKED: Final[str] = "blocked by {checks}; `weekend-loop preflight` says why"
PREFLIGHT_AFTER_ITEMS: Final[str] = "clear once the items above are done"

ROOT_HEADER: Final[str] = "Run these once as root, then run setup again:"
READY: Final[str] = "Ready."
READY_NEXT: Final[str] = "Ready. The next run starts {next}."
LEFT_ONE: Final[str] = "1 thing is left; run setup again once it is done."
LEFT_MANY: Final[str] = "{count} things are left; run setup again once they are done."
NO_ANSWER: Final[str] = "no answer"

RUNS_ON: Final[str] = "Runs are on."
RUNS_OFF: Final[str] = "Runs are off. `weekend-loop schedule on` turns them on."
RUNS_TURNED_OFF: Final[str] = "Runs are off. A run already going carries on."
RUN_LINE: Final[str] = "  {command:<8} every {moment:<10} next {next}"
RUN_GOING: Final[str] = "A {command} run is going now; `weekend-loop watch` follows it."
NO_SYSTEMD: Final[str] = (
    "No systemd user session here; `weekend-loop crontab` schedules runs instead."
)

MOMENT_FORMAT: Final[str] = "%a %H:%M"
NEXT_FORMAT: Final[str] = "%a %-d %b %H:%M"
