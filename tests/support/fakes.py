from __future__ import annotations

from pathlib import Path

FAKE_CLAUDE = """#!/usr/bin/env python3
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

here = Path(__file__).resolve().parent
arguments = sys.argv[1:]
with (here / "claude-calls.jsonl").open("a") as log:
    log.write(json.dumps(arguments) + "\\n")
if "--version" in arguments:
    print("9.9.9 (Claude Code fake)")
    raise SystemExit(0)
with (here / "claude-stdin.jsonl").open("a") as log:
    log.write(json.dumps(os.readlink("/proc/self/fd/0")) + "\\n")


def value_after(flag):
    return arguments[arguments.index(flag) + 1] if flag in arguments else None


session = value_after("--session-id") or value_after("--resume") or "no-session"
sessions = here / "claude-sessions.txt"
known = sessions.read_text().split() if sessions.is_file() else []
if "--resume" in arguments and session not in known:
    print(f"No conversation found with session ID: {session}", file=sys.stderr)
    raise SystemExit(1)
if "--session-id" in arguments and "--no-session-persistence" not in arguments:
    with sessions.open("a") as log:
        log.write(session + "\\n")


def emit(message):
    print(json.dumps(message).replace("$SESSION", session), flush=True)


def next_plan(path):
    plan = json.loads(path.read_text())
    if not isinstance(plan, list):
        return plan
    if len(plan) > 1:
        path.write_text(json.dumps(plan[1:]))
    return plan[0]


prompt = value_after("-p") or ""
match = re.search(r"issue #(\\d+)", prompt)
issue = match.group(1) if match is not None else "default"
if prompt.startswith("Reply with the single word"):
    probe = here / "claude-probe.json"
    if probe.is_file():
        plan = next_plan(probe)
        for message in plan["stream"]:
            emit(message)
        raise SystemExit(plan["exit_code"])
    emit({"type": "result", "subtype": "success", "is_error": False,
          "result": "ok", "total_cost_usd": 0.001, "permission_denials": []})
    raise SystemExit(0)
if "acceptEdits" in arguments:
    plan = next_plan(here / "claude-worker.json")
    if "files" not in plan:
        plan = plan[issue] if issue in plan else plan["default"]
    for name, content in plan["files"].items():
        target = Path.cwd() / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
    for name in plan.get("remove", []):
        (Path.cwd() / name).unlink()
    for message in plan["stream"]:
        emit(message)
    if plan.get("spawn_child"):
        child = subprocess.Popen(["sleep", "600"])
        (here / "claude-child.pid").write_text(str(child.pid))
    if plan.get("kill_orchestrator"):
        (here / "claude-worker.pid").write_text(str(os.getpid()))
        wrapper_status = Path(f"/proc/{os.getppid()}/stat").read_text()
        orchestrator = int(wrapper_status.rsplit(")", 1)[1].split()[1])
        os.kill(orchestrator, 9)
    time.sleep(plan.get("sleep_seconds", 0))
    if plan.get("forget_sessions"):
        sessions.unlink(missing_ok=True)
    raise SystemExit(plan["exit_code"])
responses_path = here / "claude-responses.json"
responses = json.loads(responses_path.read_text())
key = issue if issue in responses else "default"
response = responses[key]
if isinstance(response, list):
    if len(response) > 1:
        responses[key] = response[1:]
        responses_path.write_text(json.dumps(responses))
    response = response[0]
for message in response.get("stream", [response]):
    emit(message)
raise SystemExit(int(response.get("exit_code", 0)))
"""

FAKE_SANDBOX_TOOL = """#!/bin/sh
echo "$(basename "$0") 1.0 (fake)"
"""

FAKE_GH = """#!/usr/bin/env python3
import json
import sys
from pathlib import Path

here = Path(__file__).resolve().parent
arguments = sys.argv[1:]
with (here / "gh-calls.jsonl").open("a") as log:
    log.write(json.dumps(arguments) + "\\n")
if arguments[:1] == ["--version"]:
    print("gh version 2.100.0 (fake)")
    raise SystemExit(0)
def log_body(arguments):
    body = sys.stdin.read() if "--body-file" in arguments else ""
    with (here / "gh-bodies.jsonl").open("a") as bodies:
        bodies.write(json.dumps({"arguments": arguments, "body": body}) + "\\n")


data = json.loads((here / "gh-data.json").read_text())
if arguments[:2] == ["api", "user"]:
    print(data["login"])
elif arguments[:3] == ["api", "-X", "POST"] and arguments[3].endswith("/git/refs"):
    status = data.get("probe_status") or ("422" if data["push"] else "403")
    print(json.dumps({"message": "probe", "status": status}))
    raise SystemExit(1)
elif arguments[:1] == ["api"] and arguments[1].startswith("repos/"):
    if data.get("read_status"):
        print(json.dumps({"message": "Not Found", "status": data["read_status"]}))
        raise SystemExit(1)
    print(json.dumps(data["push"]))
elif arguments[:2] == ["pr", "list"]:
    print(json.dumps(data["pull_requests"]))
elif arguments[:2] == ["issue", "list"]:
    print(json.dumps(data["issues"]))
elif arguments[:2] == ["issue", "view"]:
    print(json.dumps({"comments": data["comments"].get(arguments[2], [])}))
elif arguments[:2] == ["pr", "create"]:
    log_body(arguments)
    print("https://github.com/owner/repo/pull/42")
elif arguments[:2] == ["issue", "comment"]:
    log_body(arguments)
    print("https://github.com/owner/repo/issues/1#issuecomment-7")
elif arguments[:2] == ["issue", "edit"]:
    print("https://github.com/owner/repo/issues/1")
elif arguments[:2] == ["issue", "create"]:
    log_body(arguments)
    print("https://github.com/owner/repo/issues/99")
else:
    print(f"fake gh cannot answer {arguments}", file=sys.stderr)
    raise SystemExit(2)
"""

FAKE_GIT = """#!/usr/bin/env python3
import json
import sys
from pathlib import Path

here = Path(__file__).resolve().parent
arguments = sys.argv[1:]
with (here / "git-calls.jsonl").open("a") as log:
    log.write(json.dumps(arguments) + "\\n")
if arguments[:1] == ["--version"]:
    print("git version 2.43.0 (fake)")
elif arguments[:1] == ["clone"]:
    destination = Path(arguments[-1])
    (destination / ".git").mkdir(parents=True, exist_ok=True)
    (destination / "src").mkdir(parents=True, exist_ok=True)
    (destination / "src" / "module.py").write_text("value = 1\\n")
    (destination / "README.md").write_text("# fake checkout\\n")
elif arguments[:1] == ["rev-parse"]:
    print("0" * 40)
raise SystemExit(0)
"""


FAKE_SYSTEMD_RUN = """#!/usr/bin/env python3
import json
import os
import sys
from pathlib import Path

here = Path(__file__).resolve().parent
arguments = sys.argv[1:]
with (here / "systemd-run-calls.jsonl").open("a") as log:
    log.write(json.dumps(arguments) + "\\n")
command = arguments[arguments.index("--") + 1 :]
os.execvp(command[0], command)
"""

FAKE_SYSTEMCTL = """#!/usr/bin/env python3
import json
import sys
from pathlib import Path

here = Path(__file__).resolve().parent
arguments = sys.argv[1:]
with (here / "systemctl-calls.jsonl").open("a") as log:
    log.write(json.dumps(arguments) + "\\n")
if "show" in arguments and "Result" in arguments:
    result = here / "systemctl-result.txt"
    print(result.read_text().strip() if result.is_file() else "success")
"""

FAKE_CHOOM = """#!/usr/bin/env python3
import os
import sys

arguments = sys.argv[1:]
command = arguments[arguments.index("--") + 1 :]
os.execvp(command[0], command)
"""

FAKE_TASKSET = """#!/usr/bin/env python3
import json
import os
import sys
from pathlib import Path

here = Path(__file__).resolve().parent
arguments = sys.argv[1:]
with (here / "taskset-calls.jsonl").open("a") as log:
    log.write(json.dumps(arguments[:2]) + "\\n")
command = arguments[2:]
os.execvp(command[0], command)
"""


def install_fake(directory: Path, name: str, script: str) -> Path:
    path = directory / name
    path.write_text(script)
    path.chmod(0o755)
    return path


def install_confinement_fakes(directory: Path) -> None:
    install_fake(directory, "systemd-run", FAKE_SYSTEMD_RUN)
    install_fake(directory, "systemctl", FAKE_SYSTEMCTL)
    install_fake(directory, "choom", FAKE_CHOOM)
    install_fake(directory, "taskset", FAKE_TASKSET)
