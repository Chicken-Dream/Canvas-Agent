from __future__ import annotations

import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..catalogue import fetch_course_description, parse_subject_and_number
from ..models import Assignment, Course, CourseOutline, User
from .client import CanvasClient
from .outline import detect_course_outline


def _parse_dt(value: str | None) -> datetime.datetime | None:
    """Canvas timestamps are ISO 8601 with a UTC offset (e.g. trailing "Z").
    The due_at/submitted_at columns are naive TIMESTAMP WITHOUT TIME ZONE,
    consistent with every other naive-UTC datetime in this schema (e.g.
    User.created_at via datetime.utcnow()) - so normalize to UTC and drop
    the tzinfo rather than storing an aware value, which asyncpg rejects
    against a naive column.
    """
    if not value:
        return None
    dt = datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
    return dt.astimezone(datetime.timezone.utc).replace(tzinfo=None)


async def sync_user_data(db: AsyncSession, user: User, client: CanvasClient) -> dict[str, int]:
    """Pulls courses, assignments, submissions, and best-effort course
    outlines from Canvas for `user` and upserts them into the local DB.
    """
    remote_courses = await client.list_courses()

    course_count = 0
    assignment_count = 0
    outlines_found = 0

    for rc in remote_courses:
        result = await db.execute(
            select(Course).where(
                Course.user_id == user.id, Course.canvas_course_id == rc["id"]
            )
        )
        course = result.scalar_one_or_none()
        if course is None:
            course = Course(user_id=user.id, canvas_course_id=rc["id"])
            db.add(course)

        course.name = rc.get("name") or rc.get("course_code") or f"Course {rc['id']}"
        course.course_code = rc.get("course_code")
        term = rc.get("term") or {}
        course.term = term.get("name")
        course.html_url = f"{client.base_url}/courses/{rc['id']}"
        course.raw_syllabus_body = rc.get("syllabus_body")

        # Course-calendar description: fetched once and cached, not
        # something that changes term-to-term for an already-synced course.
        if not course.description_text:
            subject_number = parse_subject_and_number(course.course_code, course.name)
            if subject_number:
                catalogue = await fetch_course_description(*subject_number)
                course.description_text = catalogue.get("description")
                course.description_url = catalogue.get("url")

        await db.flush()  # ensure course.id is populated for new rows
        course_count += 1

        remote_assignments = await client.list_assignments(rc["id"])
        for ra in remote_assignments:
            result = await db.execute(
                select(Assignment).where(
                    Assignment.course_id == course.id,
                    Assignment.canvas_assignment_id == ra["id"],
                )
            )
            assignment = result.scalar_one_or_none()
            if assignment is None:
                assignment = Assignment(course_id=course.id, canvas_assignment_id=ra["id"])
                db.add(assignment)

            submission = ra.get("submission") or {}
            assignment.name = ra.get("name") or f"Assignment {ra['id']}"
            assignment.description_html = ra.get("description")
            assignment.due_at = _parse_dt(ra.get("due_at"))
            assignment.points_possible = ra.get("points_possible")
            assignment.html_url = ra.get("html_url")
            assignment.submission_status = submission.get("workflow_state")
            assignment.submitted_at = _parse_dt(submission.get("submitted_at"))
            assignment.score = submission.get("score")
            assignment_count += 1

        result = await db.execute(select(CourseOutline).where(CourseOutline.course_id == course.id))
        outline = result.scalar_one_or_none()

        if outline is not None and outline.source == "user_submitted":
            # The student told us exactly where this is - fetched once on
            # submission (see routers/courses.py), not re-scraped on sync.
            if outline.confidence > 0:
                outlines_found += 1
        else:
            outline_candidate = await detect_course_outline(client, rc)
            if outline is None:
                outline = CourseOutline(course_id=course.id)
                db.add(outline)

            outline.source = outline_candidate.source
            outline.title = outline_candidate.title
            outline.canvas_url = outline_candidate.canvas_url
            outline.external_url = outline_candidate.external_url
            outline.confidence = outline_candidate.confidence
            outline.fetched_content = outline_candidate.fetched_content
            outline.detected_at = datetime.datetime.utcnow()
            if outline_candidate.confidence > 0:
                outlines_found += 1

    await db.commit()

    return {
        "courses": course_count,
        "assignments": assignment_count,
        "outlines_found": outlines_found,
    }
