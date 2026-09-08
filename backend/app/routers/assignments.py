import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..deps import get_current_user
from ..models import Assignment, Course, User
from ..schemas import AssignmentWithCourseOut

router = APIRouter(prefix="/api/assignments", tags=["assignments"])


@router.get("", response_model=list[AssignmentWithCourseOut])
async def list_assignments(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    upcoming_only: bool = Query(False, alias="upcoming"),
    days: int | None = Query(None, description="Only include assignments due within N days"),
) -> list[dict]:
    stmt = (
        select(Assignment, Course)
        .join(Course, Assignment.course_id == Course.id)
        .where(Course.user_id == user.id)
    )

    now = datetime.datetime.utcnow()
    if upcoming_only:
        stmt = stmt.where(Assignment.due_at.is_(None) | (Assignment.due_at >= now))
    if days is not None:
        horizon = now + datetime.timedelta(days=days)
        stmt = stmt.where(Assignment.due_at.is_not(None), Assignment.due_at <= horizon)

    stmt = stmt.order_by(Assignment.due_at.is_(None), Assignment.due_at)

    result = await db.execute(stmt)
    rows = result.all()

    return [
        {
            "id": a.id,
            "canvas_assignment_id": a.canvas_assignment_id,
            "name": a.name,
            "due_at": a.due_at,
            "points_possible": a.points_possible,
            "html_url": a.html_url,
            "submission_status": a.submission_status,
            "submitted_at": a.submitted_at,
            "score": a.score,
            "weight_pct": a.weight_pct,
            "course_id": c.id,
            "course_name": c.name,
            "course_code": c.course_code,
        }
        for a, c in rows
    ]
