from __future__ import annotations

import datetime
import json
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..catalogue import fetch_course_description
from ..models import Course, User

logger = logging.getLogger("app.agent.tools")

# Keep per-course outline text bounded in the agent's prompt - the full
# scrape can be tens of thousands of characters (see outline.py), which
# would be wasteful/expensive to repeat for every course on every run.
MAX_OUTLINE_CHARS_IN_PROMPT = 4_000


async def get_upcoming_assignments_json(db: AsyncSession, user: User, days: int = 14) -> str:
    """Finds every assignment belonging to `user` due within the next `days`
    days, and returns them as a JSON string in reverse chronological order
    (latest due date first) - the shape an agent (or any other consumer)
    can read directly. Also logs the same payload at INFO level, so a run
    can be audited later from the backend logs.

    This is the standalone "what's due soon" tool: it doesn't rank or
    judge urgency itself (see app.agent.urgency_agent for that), it just
    produces a clean, ordered, machine-readable snapshot of the deadlines
    an urgency judgement would need as input.
    """
    now = datetime.datetime.utcnow()
    horizon = now + datetime.timedelta(days=days)

    result = await db.execute(
        select(Course)
        .where(Course.user_id == user.id)
        .options(selectinload(Course.assignments))
    )
    courses = result.scalars().all()

    due_soon = []
    for course in courses:
        for assignment in course.assignments:
            if assignment.due_at is None or not (now <= assignment.due_at <= horizon):
                continue
            due_soon.append(
                {
                    "assignment_id": assignment.id,
                    "name": assignment.name,
                    "course_id": course.id,
                    "course_code": course.course_code,
                    "course_name": course.name,
                    "due_at": assignment.due_at.isoformat() + "Z",
                    "days_until_due": round((assignment.due_at - now).total_seconds() / 86400, 2),
                    "points_possible": assignment.points_possible,
                    "weight_pct": assignment.weight_pct,
                    "submission_status": assignment.submission_status,
                    "html_url": assignment.html_url,
                }
            )

    # Reverse chronological order: latest due date first.
    due_soon.sort(key=lambda a: a["due_at"], reverse=True)

    payload = json.dumps({"window_days": days, "generated_at": now.isoformat() + "Z", "assignments": due_soon})
    logger.info("upcoming_assignments user_id=%s count=%d payload=%s", user.id, len(due_soon), payload)
    return payload


async def get_courses_context_json(db: AsyncSession, user: User) -> str:
    """Returns each of the user's courses with its subjective difficulty
    rating and whatever outline text was resolved for it (see
    app.canvas.outline) - the material a weighting/urgency judgement needs
    beyond raw due dates.
    """
    result = await db.execute(
        select(Course).where(Course.user_id == user.id).options(selectinload(Course.outline))
    )
    courses = result.scalars().all()

    payload = []
    for course in courses:
        outline = course.outline
        payload.append(
            {
                "course_id": course.id,
                "course_code": course.course_code,
                "course_name": course.name,
                "difficulty": course.difficulty,
                "outline_confidence": outline.confidence if outline else 0.0,
                "outline_text": (outline.fetched_content or "")[:MAX_OUTLINE_CHARS_IN_PROMPT] if outline else None,
            }
        )
    return json.dumps(payload)


async def get_course_catalogue_description(subject: str, number: str) -> str:
    """Fetches the official UAlberta course calendar description for a
    course (e.g. https://apps.ualberta.ca/catalogue/course/cmput/379) -
    context on what the course actually covers, for judging how demanding
    a given assignment's subject matter likely is.
    """
    result = await fetch_course_description(subject, number)
    if result["title"] is None and result["description"] is None:
        logger.warning("catalogue fetch failed for %s", result["url"])
        return json.dumps({"url": result["url"], "error": "could not fetch course catalogue page"})
    return json.dumps(result)
