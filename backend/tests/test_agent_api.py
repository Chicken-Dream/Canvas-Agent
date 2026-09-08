from __future__ import annotations

from sqlalchemy import func, select

import app.routers.agent as agent_router
from app.agent.urgency_agent import UrgencyReportOut, UrgentAssignmentOut
from app.canvas.client import CanvasClient
from app.models import AgentUrgencyReport


async def _fake_get_self_ok(self):
    return {"id": 12345, "name": "Test Student", "primary_email": "test@ualberta.ca"}


def _canned_report(reason_suffix: str = "") -> UrgencyReportOut:
    return UrgencyReportOut(
        top_assignments=[
            UrgentAssignmentOut(
                assignment_id=1,
                name="Homework 1",
                course_code="TEST 101",
                due_at="2026-09-10T05:30:00Z",
                reason=f"Due soon and worth 20% of your grade{reason_suffix}",
            )
        ]
    )


async def _login(client, monkeypatch):
    monkeypatch.setattr(CanvasClient, "get_self", _fake_get_self_ok)
    await client.post("/auth/dev-login", json={"access_token": "fake-pat"})


async def test_agent_endpoints_require_auth(client):
    resp = await client.get("/api/agent/urgency")
    assert resp.status_code == 401

    resp = await client.post("/api/agent/urgency/evaluate")
    assert resp.status_code == 401


async def test_urgency_report_is_null_before_first_run(client, monkeypatch):
    await _login(client, monkeypatch)

    resp = await client.get("/api/agent/urgency")
    assert resp.status_code == 200
    assert resp.json() is None


async def test_evaluate_urgency_runs_agent_and_stores_result(client, db_session, monkeypatch):
    await _login(client, monkeypatch)

    async def fake_run_urgency_agent(db, user):
        return _canned_report()

    monkeypatch.setattr(agent_router, "run_urgency_agent", fake_run_urgency_agent)

    resp = await client.post("/api/agent/urgency/evaluate")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["top_assignments"]) == 1
    assert body["top_assignments"][0]["name"] == "Homework 1"
    assert "20%" in body["top_assignments"][0]["reason"]

    # Persisted, not just returned - a fresh GET (simulating a page reload)
    # sees it without re-running the agent.
    resp = await client.get("/api/agent/urgency")
    assert resp.status_code == 200
    assert resp.json()["top_assignments"][0]["name"] == "Homework 1"

    count = (await db_session.execute(select(func.count()).select_from(AgentUrgencyReport))).scalar_one()
    assert count == 1


async def test_evaluate_urgency_overwrites_rather_than_accumulates(client, db_session, monkeypatch):
    await _login(client, monkeypatch)

    call_count = {"n": 0}

    async def fake_run_urgency_agent(db, user):
        call_count["n"] += 1
        return _canned_report(reason_suffix=f" (run {call_count['n']})")

    monkeypatch.setattr(agent_router, "run_urgency_agent", fake_run_urgency_agent)

    await client.post("/api/agent/urgency/evaluate")
    second = await client.post("/api/agent/urgency/evaluate")

    assert "(run 2)" in second.json()["top_assignments"][0]["reason"]

    count = (await db_session.execute(select(func.count()).select_from(AgentUrgencyReport))).scalar_one()
    assert count == 1


async def test_evaluate_urgency_missing_credentials_returns_503(client, monkeypatch):
    await _login(client, monkeypatch)

    async def fake_run_urgency_agent(db, user):
        raise RuntimeError("AWS Bedrock credentials are not configured")

    monkeypatch.setattr(agent_router, "run_urgency_agent", fake_run_urgency_agent)

    resp = await client.post("/api/agent/urgency/evaluate")
    assert resp.status_code == 503
    assert "not configured" in resp.json()["detail"]


async def test_evaluate_urgency_agent_failure_returns_502(client, monkeypatch):
    await _login(client, monkeypatch)

    async def fake_run_urgency_agent(db, user):
        raise ValueError("Bedrock exploded")

    monkeypatch.setattr(agent_router, "run_urgency_agent", fake_run_urgency_agent)

    resp = await client.post("/api/agent/urgency/evaluate")
    assert resp.status_code == 502
    assert "Bedrock exploded" in resp.json()["detail"]
