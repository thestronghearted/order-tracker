"""Exercises the /alerts endpoint's decision logic with the heavy dependencies
(docker logs, Loki/Tempo queries, the headless agent, HTTP verification) stubbed
out, so these tests run fast and need neither Docker nor a logged-in `claude`."""

import json

import pytest
from fastapi.testclient import TestClient

from service import evidence, main
from service.agent import run_headless_agent


@pytest.fixture(autouse=True)
def stub_heavy_dependencies(monkeypatch, tmp_path):
    monkeypatch.setattr(evidence, "collect_docker_logs", lambda *a, **k: "log line 1\nTraceback...\n")
    monkeypatch.setattr(evidence, "collect_loki_logs", lambda *a, **k: "(no matching Loki entries)")
    monkeypatch.setattr(evidence, "collect_tempo_traces", lambda *a, **k: "(no matching traces)")
    monkeypatch.setattr(
        main,
        "run_headless_agent",
        lambda prompt, repo_dir, timeout_seconds=900: {
            "is_error": False,
            "result": "Investigated the bug.\nRESOLVED: fixed the date overflow.",
        },
    )
    monkeypatch.setattr(main, "verify_endpoint", lambda base_url, path: {"checked": True, "status_code": 200, "passed": True})
    monkeypatch.setattr(main, "INCIDENTS_DIR", tmp_path)
    yield


@pytest.fixture
def client():
    return TestClient(main.app)


def test_firing_alert_triggers_investigation_and_surfaces_last_line(client):
    response = client.post(
        "/alerts",
        json={
            "alerts": [
                {
                    "status": "firing",
                    "labels": {"alertname": "ResponderTest"},
                    "annotations": {"summary": "Test notification; no incident to fix", "endpoint": "/api/orders/express-1002"},
                }
            ]
        },
    )
    assert response.status_code == 200
    result = response.json()["results"][0]
    assert result["agent_last_line"] == "RESOLVED: fixed the date overflow."
    assert result["verification"]["passed"] is True


def test_resolved_alert_is_recorded_but_does_not_investigate(client, monkeypatch):
    called = []
    monkeypatch.setattr(main, "run_headless_agent", lambda *a, **k: called.append(1))
    response = client.post(
        "/alerts",
        json={"alerts": [{"status": "resolved", "labels": {"alertname": "ResponderTest"}}]},
    )
    assert response.status_code == 200
    assert "no investigation" in response.json()["results"][0]["action"]
    assert called == []


def test_the_homeworks_own_example_curl_payload_is_accepted(client):
    # Exact body from the homework's Question 5 curl example.
    body = {"alerts": [{"status": "firing", "labels": {"alertname": "ResponderTest", "test": "true"}, "annotations": {"summary": "Test notification; no incident to fix"}}]}
    response = client.post("/alerts", json=body)
    assert response.status_code == 200


def test_missing_responder_secret_is_rejected_when_one_is_configured(monkeypatch, client):
    monkeypatch.setattr(main, "RESPONDER_SECRET", "topsecret")
    response = client.post("/alerts", json={"alerts": [{"status": "firing", "labels": {"alertname": "x"}}]})
    assert response.status_code == 401


def test_matching_responder_secret_is_accepted(monkeypatch, client):
    monkeypatch.setattr(main, "RESPONDER_SECRET", "topsecret")
    response = client.post(
        "/alerts",
        json={"alerts": [{"status": "firing", "labels": {"alertname": "x"}}]},
        headers={"X-Responder-Secret": "topsecret"},
    )
    assert response.status_code == 200
