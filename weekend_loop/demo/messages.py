from typing import Final

HEADER: Final[str] = "Pocketchat at {root}"
WORKSPACE: Final[str] = "workspace"
WORKSPACE_CREATED: Final[str] = "created"
WORKSPACE_FOUND: Final[str] = "found"
UP_COMMAND: Final[str] = "`weekend-loop demo up`"
WORKSPACE_OF_YOUR_OWN: Final[str] = (
    "{root} holds a workspace of your own; pass --home with a new directory for the example"
)
BOARD: Final[str] = "board"
BOARD_SEEDED: Final[str] = (
    "{issues} issues, an open pull request linked to {linked}, hidden tests for {tested}"
)
BOARD_KEPT: Final[str] = "kept, {count} open issues"
RESET: Final[str] = "reset"
RESET_DONE: Final[str] = "removed {count} things the earlier runs left"
NEXT: Final[str] = (
    'Next: eval "$(weekend-loop demo env)", then weekend-loop weekend --ignore-window'
)
NOT_A_DEMO: Final[str] = "{root} is not the example's workspace; `weekend-loop demo up` creates one"
YOUR_OWN_HOME: Final[str] = "{root} is your own workspace; it stays"
RUN_GOING: Final[str] = "a run is going in {root}; it stays until the run ends"
REMOVE_QUESTION: Final[str] = "Remove {root} and everything in it? [y/N] "
REMOVE_NEEDS_TERMINAL: Final[str] = "Removing asks first. Run it in a terminal, or pass --yes."
REMOVE_DECLINED: Final[str] = "Nothing removed."
REMOVED: Final[str] = (
    "Removed {root}. The sandbox profile and the socket filter are shared and stay."
)
EXPORT: Final[str] = "export {name}={value}"
PATH_EXPORT: Final[str] = 'export PATH={directory}:"$PATH"'
