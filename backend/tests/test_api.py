from __future__ import annotations

import datetime

from sqlalchemy import select

from app.canvas.client import CanvasAPIError, CanvasClient
from app.models import Assignment, Course, User


async def _fake_get_self_ok(self):
    return {"id": 12345, "name": "Test Student", "primary_email": "test@ualberta.ca"}


async def _fake_get_self_rejected(self):
    raise CanvasAPIError(401, "Invalid access token.")


async def _login(client, monkeypatch):
    monkeypatch.setattr(CanvasClient, "get_self", _fake_get_self_ok)
    await client.post("/auth/dev-login", json={"access_token": "fake-pat"})


async def test_protected_endpoints_require_auth(client):
    resp = await client.get("/auth/me")
    assert resp.status_code == 401

    resp = await client.get("/api/courses")
    assert resp.status_code == 401

    resp = await client.get("/api/assignments")
    assert resp.status_code == 401


async def test_canvas_login_returns_503_without_developer_key(client):
    resp = await client.get("/auth/canvas/login", follow_redirects=False)
    assert resp.status_code == 503
    assert "Developer Key" in resp.json()["detail"]


async def test_dev_login_success_creates_user_and_sets_session(client, monkeypatch):
    monkeypatch.setattr(CanvasClient, "get_self", _fake_get_self_ok)

    resp = await client.post("/auth/dev-login", json={"access_token": "fake-pat"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["canvas_user_id"] == 12345
    assert body["name"] == "Test Student"

    me = await client.get("/auth/me")
    assert me.status_code == 200
    assert me.json()["canvas_user_id"] == 12345


async def test_dev_login_rejects_bad_token(client, monkeypatch):
    monkeypatch.setattr(CanvasClient, "get_self", _fake_get_self_rejected)

    resp = await client.post("/auth/dev-login", json={"access_token": "bogus"})
    assert resp.status_code == 401
    assert resp.json()["detail"] == "Canvas rejected this access token"


async def test_logout_clears_session(client, monkeypatch):
    monkeypatch.setattr(CanvasClient, "get_self", _fake_get_self_ok)
    await client.post("/auth/dev-login", json={"access_token": "fake-pat"})

    resp = await client.post("/auth/logout")
    assert resp.status_code == 200

    me = await client.get("/auth/me")
    assert me.status_code == 401


async def test_get_nonexistent_course_returns_404(client, monkeypatch):
    monkeypatch.setattr(CanvasClient, "get_self", _fake_get_self_ok)
    await client.post("/auth/dev-login", json={"access_token": "fake-pat"})

    resp = await client.get("/api/courses/999999")
    assert resp.status_code == 404


async def test_update_course_difficulty(client, db_session, monkeypatch):
    monkeypatch.setattr(CanvasClient, "get_self", _fake_get_self_ok)
    await client.post("/auth/dev-login", json={"access_token": "fake-pat"})

    user = (await db_session.execute(select(User).where(User.canvas_user_id == 12345))).scalar_one()
    course = Course(user_id=user.id, canvas_course_id=1, name="Test Course")
    db_session.add(course)
    await db_session.commit()
    await db_session.refresh(course)

    resp = await client.patch(f"/api/courses/{course.id}", json={"difficulty": 3})
    assert resp.status_code == 200
    assert resp.json()["difficulty"] == 3

    resp = await client.patch(f"/api/courses/{course.id}", json={"difficulty": 6})
    assert resp.status_code == 422

    resp = await client.patch(f"/api/courses/{course.id}", json={"difficulty": 0})
    assert resp.status_code == 422


async def test_update_course_difficulty_scoped_to_owning_user(client, db_session, monkeypatch):
    """A student must not be able to rate a course that isn't theirs."""
    other_user = User(canvas_user_id=99999, name="Someone Else")
    db_session.add(other_user)
    await db_session.flush()
    other_course = Course(user_id=other_user.id, canvas_course_id=7, name="Not Yours")
    db_session.add(other_course)
    await db_session.commit()
    await db_session.refresh(other_course)

    monkeypatch.setattr(CanvasClient, "get_self", _fake_get_self_ok)
    await client.post("/auth/dev-login", json={"access_token": "fake-pat"})

    resp = await client.patch(f"/api/courses/{other_course.id}", json={"difficulty": 5})
    assert resp.status_code == 404


async def test_list_assignments_upcoming_and_days_filters(client, db_session, monkeypatch):
    monkeypatch.setattr(CanvasClient, "get_self", _fake_get_self_ok)
    await client.post("/auth/dev-login", json={"access_token": "fake-pat"})

    user = (await db_session.execute(select(User).where(User.canvas_user_id == 12345))).scalar_one()
    course = Course(user_id=user.id, canvas_course_id=1, name="Test Course")
    db_session.add(course)
    await db_session.flush()

    now = datetime.datetime.utcnow()
    past = Assignment(
        course_id=course.id, canvas_assignment_id=1, name="Past Due", due_at=now - datetime.timedelta(days=2)
    )
    soon = Assignment(
        course_id=course.id, canvas_assignment_id=2, name="Due Soon", due_at=now + datetime.timedelta(days=1)
    )
    far = Assignment(
        course_id=course.id, canvas_assignment_id=3, name="Due Later", due_at=now + datetime.timedelta(days=30)
    )
    db_session.add_all([past, soon, far])
    await db_session.commit()

    resp = await client.get("/api/assignments")
    assert {a["name"] for a in resp.json()} == {"Past Due", "Due Soon", "Due Later"}

    resp = await client.get("/api/assignments", params={"upcoming": "true"})
    assert {a["name"] for a in resp.json()} == {"Due Soon", "Due Later"}

    resp = await client.get("/api/assignments", params={"upcoming": "true", "days": 7})
    assert {a["name"] for a in resp.json()} == {"Due Soon"}


async def test_assignments_are_scoped_to_the_logged_in_user(client, db_session, monkeypatch):
    other_user = User(canvas_user_id=54321, name="Other Student")
    db_session.add(other_user)
    await db_session.flush()
    other_course = Course(user_id=other_user.id, canvas_course_id=2, name="Other Course")
    db_session.add(other_course)
    await db_session.flush()
    db_session.add(Assignment(course_id=other_course.id, canvas_assignment_id=1, name="Not Mine"))
    await db_session.commit()

    monkeypatch.setattr(CanvasClient, "get_self", _fake_get_self_ok)
    await client.post("/auth/dev-login", json={"access_token": "fake-pat"})

    resp = await client.get("/api/assignments")
    assert resp.json() == []


async def test_sync_endpoint_pulls_courses_and_assignments_end_to_end(client, monkeypatch):
    """Drives POST /api/sync through the real router + dependency chain,
    with only the outbound Canvas calls faked - the closest thing to the
    live smoke test that still runs offline and deterministically.
    """
    monkeypatch.setattr(CanvasClient, "get_self", _fake_get_self_ok)

    async def fake_list_courses(self):
        return [
            {
                "id": 501,
                "name": "Intro to Testing",
                "course_code": "TEST 101",
                "term": {"name": "Fall Term 2026"},
                "syllabus_body": None,
            }
        ]

    async def fake_list_assignments(self, course_id):
        return [
            {
                "id": 9001,
                "name": "Homework 1",
                "due_at": "2026-09-10T05:30:00Z",
                "points_possible": 10.0,
                "html_url": "https://canvas.test/courses/501/assignments/9001",
                "submission": {"workflow_state": "unsubmitted"},
            }
        ]

    async def fake_list_modules_with_items(self, course_id):
        return []

    async def fake_list_pages(self, course_id):
        return []

    monkeypatch.setattr(CanvasClient, "list_courses", fake_list_courses)
    monkeypatch.setattr(CanvasClient, "list_assignments", fake_list_assignments)
    monkeypatch.setattr(CanvasClient, "list_modules_with_items", fake_list_modules_with_items)
    monkeypatch.setattr(CanvasClient, "list_pages", fake_list_pages)

    await client.post("/auth/dev-login", json={"access_token": "fake-pat"})

    resp = await client.post("/api/sync")
    assert resp.status_code == 200
    assert resp.json() == {"courses": 1, "assignments": 1, "outlines_found": 0}

    courses = (await client.get("/api/courses")).json()
    assert len(courses) == 1
    assert courses[0]["name"] == "Intro to Testing"

    assignments = (await client.get("/api/assignments")).json()
    assert len(assignments) == 1
    assert assignments[0]["name"] == "Homework 1"
    assert assignments[0]["course_code"] == "TEST 101"


async def test_submit_course_outline_fetches_and_stores_canvas_page(client, db_session, monkeypatch):
    await _login(client, monkeypatch)

    user = (await db_session.execute(select(User).where(User.canvas_user_id == 12345))).scalar_one()
    course = Course(user_id=user.id, canvas_course_id=34230, name="Test Course")
    db_session.add(course)
    await db_session.commit()
    await db_session.refresh(course)

    async def fake_get_page(self, course_id, url_or_slug):
        assert course_id == 34230  # the *Canvas* id, not our internal PK
        assert url_or_slug == "course-outline"
        return {"body": "<p>Real grading breakdown here.</p>"}

    monkeypatch.setattr(CanvasClient, "get_page", fake_get_page)

    resp = await client.post(
        f"/api/courses/{course.id}/outline",
        json={"url": "https://canvas.ualberta.ca/courses/34230/pages/course-outline?module_item_id=1"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["source"] == "user_submitted"
    assert body["confidence"] == 1.0
    assert "Real grading breakdown" in body["fetched_content"]

    # Persisted - a fresh course-detail fetch sees it without resubmitting.
    detail = (await client.get(f"/api/courses/{course.id}")).json()
    assert detail["outline"]["source"] == "user_submitted"


async def test_submit_course_outline_replaces_existing_outline(client, db_session, monkeypatch):
    await _login(client, monkeypatch)

    user = (await db_session.execute(select(User).where(User.canvas_user_id == 12345))).scalar_one()
    course = Course(user_id=user.id, canvas_course_id=1, name="Test Course")
    db_session.add(course)
    await db_session.commit()
    await db_session.refresh(course)

    import app.canvas.outline as outline_mod

    async def fake_fetch_external_text(url):
        return f"CONTENT FOR {url}"

    monkeypatch.setattr(outline_mod, "_fetch_external_text", fake_fetch_external_text)

    first = await client.post(f"/api/courses/{course.id}/outline", json={"url": "https://example.com/v1"})
    assert first.json()["external_url"] == "https://example.com/v1"

    second = await client.post(f"/api/courses/{course.id}/outline", json={"url": "https://example.com/v2"})
    assert second.status_code == 200
    assert second.json()["external_url"] == "https://example.com/v2"
    assert "v2" in second.json()["fetched_content"]

    detail = (await client.get(f"/api/courses/{course.id}")).json()
    assert detail["outline"]["external_url"] == "https://example.com/v2"


async def test_submit_course_outline_rejects_malformed_url(client, monkeypatch):
    await _login(client, monkeypatch)

    resp = await client.post("/api/courses/1/outline", json={"url": "not-a-url"})
    assert resp.status_code == 422


async def test_submit_course_outline_requires_ownership(client, db_session, monkeypatch):
    other_user = User(canvas_user_id=99999, name="Someone Else")
    db_session.add(other_user)
    await db_session.flush()
    other_course = Course(user_id=other_user.id, canvas_course_id=7, name="Not Yours")
    db_session.add(other_course)
    await db_session.commit()
    await db_session.refresh(other_course)

    await _login(client, monkeypatch)

    resp = await client.post(f"/api/courses/{other_course.id}/outline", json={"url": "https://example.com/x"})
    assert resp.status_code == 404


async def test_submit_course_outline_requires_auth(client):
    resp = await client.post("/api/courses/1/outline", json={"url": "https://example.com/x"})
    assert resp.status_code == 401
