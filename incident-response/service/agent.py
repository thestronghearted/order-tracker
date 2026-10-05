"""Invokes the headless coding agent (Claude Code CLI) to investigate and fix
an incident, scoped to the order-tracker repository only.

Deliberately does *not* tell the agent what the bug is - only the alert and the
evidence collected at the time it fired, same as a real on-call agent would get.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

PROMPT_TEMPLATE = """\
You are an automatic incident responder for the "order-tracker" service, a small
FastAPI app. A monitoring alert just fired.

## Alert
{alert_json}

## Evidence collected at alert time

### Recent application logs (docker compose logs app)
```
{docker_logs}
```

### Matching log entries from Loki (best effort, may be empty)
```
{loki_logs}
```

### Matching error traces from Tempo (best effort, may be empty)
```
{tempo_traces}
```

## Your job
0. If the alert is explicitly a test notification with no real incident (for
   example labelled test or "no incident to fix") and the evidence shows no real
   errors, do not change any code: just say so and finish with
   `RESOLVED: test notification received, no incident to fix.`
1. Investigate the order-tracker source in this repository to find the root
   cause of the error(s) reflected in the evidence above. Read the actual code
   - do not guess from the alert name alone.
2. Apply the smallest correct fix in the source code.
3. If none of the existing tests in tests/test_api.py would have caught this
   bug, add a regression test that does.
4. Run the test suite: `uv run --frozen pytest -q`. It must pass.
5. Rebuild and restart the app: `docker compose up --build -d --wait`.
6. Re-issue the exact request that was failing (see the alert's `endpoint` /
   `request_path` annotation, or infer it from the evidence) and confirm the
   fix actually resolves it - paste the response you got.
7. Finish your final message with exactly one line starting with either:
   `RESOLVED: <one-sentence summary of the root cause and fix>`, or
   `ESCALATE: <why you could not fix it confidently>`
   if you are not confident the fix is correct and verified.

Work only within this repository ({repo_dir}). Do not modify anything outside it,
and do not rewrite git history - a normal commit of your fix at the end is
welcome but optional.
"""


def build_prompt(alert: dict, docker_logs: str, loki_logs: str, tempo_traces: str, repo_dir: Path) -> str:
    return PROMPT_TEMPLATE.format(
        alert_json=json.dumps(alert, indent=2),
        docker_logs=docker_logs[-8000:],
        loki_logs=loki_logs[-4000:],
        tempo_traces=tempo_traces[-4000:],
        repo_dir=repo_dir,
    )


def run_headless_agent(prompt: str, repo_dir: Path, timeout_seconds: int = 900) -> dict:
    """Runs `claude -p` headlessly, scoped to repo_dir. Returns the parsed JSON result.

    --permission-mode bypassPermissions: headless mode has no TTY to approve
    prompts with, so this is required for the agent to do anything at all; per
    the homework's own design (and the user's explicit sign-off), it is scoped
    to this repository only via cwd/--add-dir, not the whole machine.
    """
    # On Windows the npm shim is claude.cmd, which CreateProcess cannot find by bare
    # name, and a multi-line prompt as an argument is mangled by cmd.exe - so resolve
    # the full path and send the prompt on stdin instead.
    cmd = [
        shutil.which("claude") or "claude",
        "-p",
        "--output-format", "json",
        "--permission-mode", "bypassPermissions",
        "--add-dir", str(repo_dir),
    ]
    try:
        completed = subprocess.run(
            cmd,
            input=prompt,
            cwd=repo_dir,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        return {
            "is_error": True,
            "result": f"ESCALATE: headless agent timed out after {timeout_seconds}s",
            "stdout": (exc.stdout or b"").decode() if isinstance(exc.stdout, bytes) else (exc.stdout or ""),
            "stderr": (exc.stderr or b"").decode() if isinstance(exc.stderr, bytes) else (exc.stderr or ""),
        }

    stdout = completed.stdout.strip()
    try:
        parsed = json.loads(stdout)
    except json.JSONDecodeError:
        parsed = {
            "is_error": completed.returncode != 0,
            "result": stdout or completed.stderr,
        }
    parsed.setdefault("returncode", completed.returncode)
    parsed.setdefault("stderr", completed.stderr)
    return parsed
