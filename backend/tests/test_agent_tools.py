from __future__ import annotations

import datetime
import json
import logging

import httpx
import pytest

from app.agent import tools as tools_mod
from app.agent.tools import (
    get_course_catalogue_description,
    get_courses_context_json,
    get_upcoming_assignments_json,
)
from app.models import Assignment, Course, CourseOutline, User


async def test_get_upcoming_assignments_json_filters_window_and_orders_reverse_chronologically(db_session):
    user = User(canvas_user_id=1, name="Test Student")
    db_session.add(user)
    await db_session.flush()
    course = Course(user_id=user.id, canvas_course_id=1, name="Test Course", course_code="TEST 101")
    db_session.add(course)
    await db_session.flush()

    now = datetime.datetime.utcnow()
    overdue = Assignment(course_id=course.id, canvas_assignment_id=1, name="Overdue", due_at=now - datetime.timedelta(days=1))
    soon = Assignment(course_id=course.id, canvas_assignment_id=2, name="Due in 2 days", due_at=now + datetime.timedelta(days=2))
    mid = Assignment(course_id=course.id, canvas_assignment_id=3, name="Due in 10 days", due_at=now + datetime.timedelta(days=10))
    far = Assignment(course_id=course.id, canvas_assignment_id=4, name="Due in 30 days", due_at=now + datetime.timedelta(days=30))
    no_date = Assignment(course_id=course.id, canvas_assignment_id=5, name="No due date", due_at=None)
    db_session.add_all([overdue, soon, mid, far, no_date])
    await db_session.commit()

    payload = json.loads(await get_upcoming_assignments_json(db_session, user, days=14))

    names_in_order = [a["name"] for a in payload["assignments"]]
    assert names_in_order == ["Due in 10 days", "Due in 2 days"]
    assert payload["window_days"] == 14


async def test_get_upcoming_assignments_json_includes_course_and_grading_fields(db_session):
    user = User(canvas_user_id=2, name="Test Student 2")
    db_session.add(user)
    await db_session.flush()
    course = Course(user_id=user.id, canvas_course_id=1, name="Intro to Testing", course_code="TEST 101")
    db_session.add(course)
    await db_session.flush()

    now = datetime.datetime.utcnow()
    db_session.add(
        Assignment(
            course_id=course.id,
            canvas_assignment_id=1,
            name="Homework 1",
            due_at=now + datetime.timedelta(days=3),
            points_possible=10.0,
            weight_pct=20.0,
            submission_status="unsubmitted",
        )
    )
    await db_session.commit()

    payload = json.loads(await get_upcoming_assignments_json(db_session, user, days=14))
    assignment = payload["assignments"][0]

    assert assignment["course_code"] == "TEST 101"
    assert assignment["points_possible"] == 10.0
    assert assignment["weight_pct"] == 20.0
    assert assignment["submission_status"] == "unsubmitted"
    assert assignment["days_until_due"] == pytest.approx(3.0, abs=0.05)


async def test_get_upcoming_assignments_json_logs_the_payload(db_session, caplog):
    user = User(canvas_user_id=3, name="Test Student 3")
    db_session.add(user)
    await db_session.flush()

    with caplog.at_level(logging.INFO, logger="app.agent.tools"):
        await get_upcoming_assignments_json(db_session, user, days=14)

    assert any("upcoming_assignments" in record.message for record in caplog.records)


async def test_get_courses_context_json_includes_difficulty_and_truncated_outline(db_session):
    user = User(canvas_user_id=4, name="Test Student 4")
    db_session.add(user)
    await db_session.flush()
    course = Course(user_id=user.id, canvas_course_id=1, name="Test Course", course_code="TEST 101", difficulty=4)
    db_session.add(course)
    await db_session.flush()
    long_text = "x" * (tools_mod.MAX_OUTLINE_CHARS_IN_PROMPT + 500)
    db_session.add(CourseOutline(course_id=course.id, source="hardcoded", confidence=1.0, fetched_content=long_text))
    await db_session.commit()

    payload = json.loads(await get_courses_context_json(db_session, user))

    assert payload[0]["difficulty"] == 4
    assert payload[0]["outline_confidence"] == 1.0
    assert len(payload[0]["outline_text"]) == tools_mod.MAX_OUTLINE_CHARS_IN_PROMPT


async def test_get_courses_context_json_handles_course_without_outline(db_session):
    user = User(canvas_user_id=5, name="Test Student 5")
    db_session.add(user)
    await db_session.flush()
    db_session.add(Course(user_id=user.id, canvas_course_id=1, name="No Outline Yet"))
    await db_session.commit()

    payload = json.loads(await get_courses_context_json(db_session, user))

    assert payload[0]["outline_confidence"] == 0.0
    assert payload[0]["outline_text"] is None


CATALOGUE_HTML = """
<div class="container">
  <h1 class="mb-1">CMPUT 379 - Operating System Concepts</h1>
  <h2 class="fs-5 mb-1">3 units (fi 6)(EITHER, 3-0-3)</h2>
  <p>Faculty of Science</p>
  <p>Introduction to the structure, components, and concepts behind modern operating systems.</p>
  <div class="row">Term info here</div>
</div>
"""


async def test_get_course_catalogue_description_parses_real_page_structure(monkeypatch):
    class FakeResponse:
        text = CATALOGUE_HTML
        status_code = 200

        def raise_for_status(self):
            pass

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def get(self, url):
            return FakeResponse()

    import app.catalogue as catalogue_module

    monkeypatch.setattr(catalogue_module.httpx, "AsyncClient", FakeClient)

    result = json.loads(await get_course_catalogue_description("cmput", "379"))

    assert result["title"] == "CMPUT 379 - Operating System Concepts"
    assert "Introduction to the structure" in result["description"]
    assert "cmput/379" in result["url"]


async def test_get_course_catalogue_description_handles_fetch_failure(monkeypatch):
    class RaisingClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def get(self, url):
            raise httpx.ConnectError("simulated failure")

    import app.catalogue as catalogue_module

    monkeypatch.setattr(catalogue_module.httpx, "AsyncClient", RaisingClient)

    result = json.loads(await get_course_catalogue_description("cmput", "999"))

    assert "error" in result
