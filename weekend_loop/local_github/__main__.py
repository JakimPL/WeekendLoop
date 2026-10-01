import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

from weekend_loop.local_github.commands import Invocation, run_gh

ROOT_OPTION: Final[str] = "--root"
USAGE: Final[str] = "usage: python -m weekend_loop.local_github --root <board root> <gh arguments>"


def read_stdin() -> str:
    return sys.stdin.read()


def main() -> None:
    arguments = sys.argv[1:]
    if arguments[:1] != [ROOT_OPTION] or len(arguments) < 2:
        print(USAGE, file=sys.stderr)
        raise SystemExit(2)
    answer = run_gh(
        Invocation(
            root=Path(arguments[1]),
            arguments=arguments[2:],
            read_stdin=read_stdin,
            environment=dict(os.environ),
            working_directory=Path.cwd(),
            now=datetime.now(UTC).replace(microsecond=0),
        )
    )
    sys.stdout.write(answer.stdout)
    sys.stderr.write(answer.stderr)
    raise SystemExit(answer.exit_code)


main()
