"""Incident responder: receives Grafana alert webhooks (or the homework's manual
test curl) at POST /alerts, gathers evidence, and runs a headless coding agent to
investigate, fix, and verify - scoped to the order-tracker repository.

Runs on the host (not in Docker): it needs the host's `docker` CLI to rebuild and
restart the app, and the host's `claude` CLI with its own login, neither of which
should be duplicated into a container image.
"""

from __future__ import annotations

import json
import logging
import os
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import Body, FastAPI, Header, HTTPException

from . import evidence
from .agent import build_prompt, run_headless_agent
from .policy import InFlightTracker
from .verify import verify_endpoint

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("incident-response")

REPO_DIR = Path(os.getenv("ORDER_TRACKER_REPO", Path(__file__).resolve().parents[2])).resolve()
INCIDENTS_DIR = Path(__file__).resolve().parent.parent / "incidents"
APP_BASE_URL = os.getenv("ORDER_TRACKER_URL", "http://localhost:8000")
LOKI_URL = os.getenv("LOKI_URL", "http://localhost:3100")
TEMPO_URL = os.getenv("TEMPO_URL", "http://localhost:3200")
# Optional: unset by default so the homework's own example curl (no headers)
# works unmodified. Set it, and configure the same value as a custom header on
# Grafana's webhook contact point, to require it for real alert delivery.
RESPONDER_SECRET = os.getenv("RESPONDER_SECRET")
AGENT_TIMEOUT_SECONDS = int(os.getenv("AGENT_TIMEOUT_SECONDS", "900"))

app = FastAPI(title="Order Tracker Incident Responder")
in_flight = InFlightTracker()


@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok"}


@app.post("/alerts")
def receive_alert(payload: dict = Body(...), x_responder_secret: str | None = Header(default=None)):
    # A plain def (not async) so FastAPI runs it in a worker thread: an investigation
    # takes minutes, and Grafana retries slow webhooks. If this blocked the event loop
    # the retries would queue up and run one after another, defeating the in-flight
    # de-duplication instead of being skipped immediately.
    if RESPONDER_SECRET and x_responder_secret != RESPONDER_SECRET:
        raise HTTPException(401, "invalid or missing X-Responder-Secret")

    alerts = payload.get("alerts") or [payload]  # tolerate a bare single-alert body too
    results = [handle_one_alert(alert) for alert in alerts]
    return {"handled": len(results), "results": results}


def handle_one_alert(alert: dict[str, Any]) -> dict[str, Any]:
    labels = alert.get("labels") or {}
    alert_name = labels.get("alertname", "unknown")
    status = alert.get("status", "firing")
    incident_id = f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:8]}"
    incident_dir = INCIDENTS_DIR / incident_id
    incident_dir.mkdir(parents=True, exist_ok=True)
    (incident_dir / "alert.json").write_text(json.dumps(alert, indent=2))

    logger.info("incident %s: alert=%s status=%s", incident_id, alert_name, status)

    if status != "firing":
        record = {
            "incident_id": incident_id,
            "alert_name": alert_name,
            "status": status,
            "action": "recorded, no investigation (alert is not firing)",
        }
        (incident_dir / "result.json").write_text(json.dumps(record, indent=2))
        return record

    if not in_flight.try_start(alert_name):
        record = {
            "incident_id": incident_id,
            "alert_name": alert_name,
            "status": status,
            "action": "skipped: an investigation for this alert is already in progress",
        }
        (incident_dir / "result.json").write_text(json.dumps(record, indent=2))
        return record

    try:
        return investigate(incident_id, incident_dir, alert, alert_name)
    finally:
        in_flight.finish(alert_name)


def investigate(incident_id: str, incident_dir: Path, alert: dict, alert_name: str) -> dict[str, Any]:
    docker_logs = evidence.collect_docker_logs(REPO_DIR)
    loki_logs = evidence.collect_loki_logs(LOKI_URL)
    tempo_traces = evidence.collect_tempo_traces(TEMPO_URL)
    (incident_dir / "evidence-docker-logs.txt").write_text(docker_logs)
    (incident_dir / "evidence-loki.txt").write_text(loki_logs)
    (incident_dir / "evidence-tempo.txt").write_text(tempo_traces)

    annotations = alert.get("annotations") or {}
    labels = alert.get("labels") or {}
    endpoint = annotations.get("endpoint") or labels.get("endpoint")

    prompt = build_prompt(alert, docker_logs, loki_logs, tempo_traces, REPO_DIR)
    (incident_dir / "prompt.txt").write_text(prompt)

    started = time.time()
    agent_result = run_headless_agent(prompt, REPO_DIR, timeout_seconds=AGENT_TIMEOUT_SECONDS)
    duration = time.time() - started
    (incident_dir / "agent-result.json").write_text(json.dumps(agent_result, indent=2))

    agent_text = str(agent_result.get("result", ""))
    last_line = next((line for line in reversed(agent_text.splitlines()) if line.strip()), "")

    verification = verify_endpoint(APP_BASE_URL, endpoint) if endpoint else {
        "checked": False,
        "reason": "no endpoint on the alert to verify",
    }

    record = {
        "incident_id": incident_id,
        "alert_name": alert_name,
        "status": "firing",
        "agent_duration_seconds": round(duration, 1),
        "agent_is_error": agent_result.get("is_error"),
        "agent_response": agent_text,
        "agent_last_line": last_line,
        "verification": verification,
    }
    (incident_dir / "result.json").write_text(json.dumps(record, indent=2))
    logger.info("incident %s done in %.1fs: %s", incident_id, duration, last_line)
    return record
