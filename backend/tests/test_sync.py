from __future__ import annotations

import datetime

import pytest
from sqlalchemy import func, select

import app.canvas.sync as sync_mod
from app.canvas.sync import _parse_dt, sync_user_data
from app.models import Assignment, Course, CourseOutline, User


@pytest.fixture(autouse=True)
def no_real_catalogue_fetch(monkeypatch):
    """sync_user_data fetches a course-catalogue description for any course
    whose code/name looks like "SUBJECT NNN" - which "TEST 101" below does.
    Stub it so tests never hit the real network.
    """

    async def fake_fetch(subject: str, number: str) -> dict:
        return {
            "url": f"https://apps.ualberta.ca/catalogue/course/{subject}/{number}",
            "title": f"{subject.upper()} {number}",
            "description": "Fake catalogue description.",
        }

    monkeypatch.setattr(sync_mod, "fetch_course_description", fake_fetch)


def test_parse_dt_strips_tzinfo_for_the_naive_db_columns():
    """due_at/submitted_at are TIMESTAMP WITHOUT TIME ZONE columns (like
    every other naive-UTC datetime in this schema). asyncpg rejects an
    aware datetime against a naive column - SQLite silently accepts it,
    which is why this needs its own direct assertion rather than relying
    on the (SQLite-backed) integration tests below to catch it.
    """
    result = _parse_dt("2026-09-10T05:30:00Z")
    assert result == datetime.datetime(2026, 9, 10, 5, 30, 0)
    assert result.tzinfo is None


def test_parse_dt_converts_non_utc_offsets_to_utc_before_stripping():
    result = _parse_dt("2026-09-10T01:30:00-04:00")
    assert result == datetime.datetime(2026, 9, 10, 5, 30, 0)
    assert result.tzinfo is None


def test_parse_dt_handles_none():
    assert _parse_dt(None) is None


class FakeCanvasClient:
    base_url = "https://canvas.test"

    def __init__(self, courses, assignments_by_course):
        self._courses = courses
        self._assignments_by_course = assignments_by_course

    async def list_courses(self):
        return self._courses

    async def list_assignments(self, course_id):
        return self._assignments_by_course.get(course_id, [])

    async def list_modules_with_items(self, course_id):
        return []

    async def list_pages(self, course_id):
        return []

    async def get_page(self, course_id, url_or_slug):
        return None

    async def get_file(self, file_id):
        return None


def _make_client():
    courses = [
        {
            "id": 501,
            "name": "Intro to Testing",
            "course_code": "TEST 101",
            "term": {"name": "Fall Term 2026"},
            "syllabus_body": "<p>Please review the syllabus for grading policy.</p>",
        }
    ]
    assignments_by_course = {
        501: [
            {
                "id": 9001,
                "name": "Homework 1",
                "description": "<p>Do the thing</p>",
                "due_at": "2026-09-10T05:30:00Z",
                "points_possible": 10.0,
                "html_url": "https://canvas.test/courses/501/assignments/9001",
                "submission": {
                    "workflow_state": "unsubmitted",
                    "submitted_at": None,
                    "score": None,
                },
            }
        ]
    }
    return FakeCanvasClient(courses, assignments_by_course)


async def test_sync_creates_course_assignment_and_outline(db_session):
    user = User(canvas_user_id=1, name="Test Student")
    db_session.add(user)
    await db_session.flush()

    result = await sync_user_data(db_session, user, _make_client())

    assert result == {"courses": 1, "assignments": 1, "outlines_found": 1}

    course = (await db_session.execute(select(Course).where(Course.user_id == user.id))).scalar_one()
    assert course.name == "Intro to Testing"
    assert course.course_code == "TEST 101"
    assert course.term == "Fall Term 2026"

    assignment = (
        await db_session.execute(select(Assignment).where(Assignment.course_id == course.id))
    ).scalar_one()
    assert assignment.name == "Homework 1"
    assert assignment.points_possible == 10.0
    assert assignment.submission_status == "unsubmitted"
    assert assignment.due_at.year == 2026 and assignment.due_at.month == 9 and assignment.due_at.day == 10

    outline = (
        await db_session.execute(select(CourseOutline).where(CourseOutline.course_id == course.id))
    ).scalar_one()
    assert outline.source == "syllabus_body"
    assert outline.confidence == 0.4


async def test_sync_is_idempotent_on_rerun(db_session):
    user = User(canvas_user_id=2, name="Test Student 2")
    db_session.add(user)
    await db_session.flush()

    client = _make_client()
    first = await sync_user_data(db_session, user, client)
    second = await sync_user_data(db_session, user, client)

    assert first == second == {"courses": 1, "assignments": 1, "outlines_found": 1}

    course_count = (
        await db_session.execute(select(func.count()).select_from(Course).where(Course.user_id == user.id))
    ).scalar_one()
    assignment_count = (
        await db_session.execute(select(func.count()).select_from(Assignment))
    ).scalar_one()
    outline_count = (
        await db_session.execute(select(func.count()).select_from(CourseOutline))
    ).scalar_one()

    assert course_count == 1
    assert assignment_count == 1
    assert outline_count == 1


async def test_sync_updates_existing_rows_instead_of_duplicating(db_session):
    user = User(canvas_user_id=3, name="Test Student 3")
    db_session.add(user)
    await db_session.flush()

    courses = [
        {
            "id": 501,
            "name": "Intro to Testing",
            "course_code": "TEST 101",
            "term": {"name": "Fall Term 2026"},
            "syllabus_body": None,
        }
    ]
    assignments_by_course = {
        501: [
            {
                "id": 9001,
                "name": "Homework 1",
                "due_at": "2026-09-10T05:30:00Z",
                "points_possible": 10.0,
                "submission": {"workflow_state": "unsubmitted"},
            }
        ]
    }
    client = FakeCanvasClient(courses, assignments_by_course)
    await sync_user_data(db_session, user, client)

    # Simulate the student submitting the assignment before the next sync.
    assignments_by_course[501][0]["submission"] = {
        "workflow_state": "graded",
        "submitted_at": "2026-09-09T12:00:00Z",
        "score": 9.5,
    }
    await sync_user_data(db_session, user, client)

    assignment = (await db_session.execute(select(Assignment))).scalar_one()
    assert assignment.submission_status == "graded"
    assert assignment.score == 9.5


async def test_sync_populates_course_description(db_session):
    user = User(canvas_user_id=4, name="Test Student 4")
    db_session.add(user)
    await db_session.flush()

    await sync_user_data(db_session, user, _make_client())

    course = (await db_session.execute(select(Course).where(Course.user_id == user.id))).scalar_one()
    assert course.description_text == "Fake catalogue description."
    assert course.description_url == "https://apps.ualberta.ca/catalogue/course/test/101"


async def test_sync_fetches_course_description_only_once(db_session, monkeypatch):
    user = User(canvas_user_id=5, name="Test Student 5")
    db_session.add(user)
    await db_session.flush()

    client = _make_client()
    await sync_user_data(db_session, user, client)

    async def different_fetch(subject, number):
        return {"url": "https://example.com/changed", "title": "Changed", "description": "Changed description."}

    monkeypatch.setattr(sync_mod, "fetch_course_description", different_fetch)
    await sync_user_data(db_session, user, client)

    course = (await db_session.execute(select(Course).where(Course.user_id == user.id))).scalar_one()
    assert course.description_text == "Fake catalogue description."  # unchanged - cached from first sync


async def test_sync_does_not_redetect_outline_once_user_submitted(db_session):
    user = User(canvas_user_id=6, name="Test Student 6")
    db_session.add(user)
    await db_session.flush()

    client = _make_client()
    await sync_user_data(db_session, user, client)

    course = (await db_session.execute(select(Course).where(Course.user_id == user.id))).scalar_one()
    outline = (
        await db_session.execute(select(CourseOutline).where(CourseOutline.course_id == course.id))
    ).scalar_one()
    # Simulate the student having submitted their own outline link.
    outline.source = "user_submitted"
    outline.fetched_content = "MY MANUALLY SUBMITTED OUTLINE TEXT"
    outline.confidence = 1.0
    await db_session.commit()

    result = await sync_user_data(db_session, user, client)

    assert result["outlines_found"] == 1
    await db_session.refresh(outline)
    assert outline.source == "user_submitted"
    assert outline.fetched_content == "MY MANUALLY SUBMITTED OUTLINE TEXT"
