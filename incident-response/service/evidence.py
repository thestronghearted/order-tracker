"""Collects the evidence an investigating agent needs.

`collect_docker_logs` always works (it just shells out to `docker compose logs`)
and is the reliable backbone. The Loki/Tempo queries are best-effort extras: if
the observability stack isn't reachable, or a query API detail is off, they
degrade to a clearly-labelled empty result rather than failing the incident.
"""

from __future__ import annotations

import subprocess
import time
from pathlib import Path

import httpx


def collect_docker_logs(repo_dir: Path, service: str = "app", tail: int = 300) -> str:
    try:
        result = subprocess.run(
            ["docker", "compose", "logs", "--no-color", f"--tail={tail}", service],
            cwd=repo_dir,
            capture_output=True,
            text=True,
            timeout=30,
        )
        return result.stdout or result.stderr or "(no output)"
    except Exception as exc:  # pragma: no cover - best-effort diagnostics
        return f"(failed to collect docker compose logs: {exc!r})"


def collect_loki_logs(loki_url: str, minutes: int = 15, limit: int = 200) -> str:
    try:
        response = httpx.get(
            f"{loki_url}/loki/api/v1/query_range",
            params={"query": '{service_name="order-tracker"}', "limit": limit, "since": f"{minutes}m"},
            timeout=5,
        )
        response.raise_for_status()
        lines = [
            line
            for stream in response.json().get("data", {}).get("result", [])
            for _, line in stream.get("values", [])
        ]
        return "\n".join(lines) if lines else "(no matching Loki entries)"
    except Exception as exc:
        return f"(Loki query failed or unavailable: {exc!r})"


def collect_tempo_traces(tempo_url: str, minutes: int = 15, limit: int = 20) -> str:
    try:
        now = int(time.time())
        response = httpx.get(
            f"{tempo_url}/api/search",
            params={
                "tags": "service.name=order-tracker",
                "start": now - minutes * 60,
                "end": now,
                "limit": limit,
            },
            timeout=5,
        )
        response.raise_for_status()
        return response.text
    except Exception as exc:
        return f"(Tempo query failed or unavailable: {exc!r})"
