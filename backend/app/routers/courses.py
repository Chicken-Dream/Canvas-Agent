import datetime
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..canvas.client import CanvasClient
from ..canvas.outline import resolve_outline_from_url
from ..database import get_db
from ..deps import get_canvas_client_for_user, get_current_user
from ..models import Course, CourseOutline, User
from ..schemas import CourseDetailOut, CourseOut, CourseOutlineDetailOut, CourseUpdateIn, OutlineSubmitIn

router = APIRouter(prefix="/api/courses", tags=["courses"])


@router.get("", response_model=list[CourseOut])
async def list_courses(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[Course]:
    result = await db.execute(
        select(Course)
        .where(Course.user_id == user.id)
        .options(selectinload(Course.outline))
        .order_by(Course.name)
    )
    return list(result.scalars().all())


@router.get("/{course_id}", response_model=CourseDetailOut)
async def get_course(
    course_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Course:
    result = await db.execute(
        select(Course)
        .where(Course.id == course_id, Course.user_id == user.id)
        .options(selectinload(Course.outline), selectinload(Course.assignments))
    )
    course = result.scalar_one_or_none()
    if course is None:
        raise HTTPException(status_code=404, detail="Course not found")
    course.assignments.sort(key=lambda a: (a.due_at is None, a.due_at))
    return course


@router.patch("/{course_id}", response_model=CourseOut)
async def update_course(
    course_id: int,
    payload: CourseUpdateIn,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Course:
    """Lets the student set a subjective difficulty rating for a course -
    input the future Strands prioritization agent will use alongside
    deadlines and outline-derived assignment weights.
    """
    result = await db.execute(
        select(Course)
        .where(Course.id == course_id, Course.user_id == user.id)
        .options(selectinload(Course.outline))
    )
    course = result.scalar_one_or_none()
    if course is None:
        raise HTTPException(status_code=404, detail="Course not found")

    if payload.difficulty is not None:
        if not 1 <= payload.difficulty <= 5:
            raise HTTPException(status_code=422, detail="difficulty must be between 1 and 5")
        course.difficulty = payload.difficulty

    await db.commit()
    await db.refresh(course)
    return course


@router.post("/{course_id}/outline", response_model=CourseOutlineDetailOut)
async def submit_course_outline(
    course_id: int,
    payload: OutlineSubmitIn,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    client: CanvasClient = Depends(get_canvas_client_for_user),
) -> CourseOutline:
    """The student tells us where their course outline actually is. We
    fetch and store its content once here, rather than on every sync -
    re-submitting (e.g. to correct a wrong link) re-fetches and overwrites.
    """
    url = payload.url.strip()
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise HTTPException(status_code=422, detail="Please provide a full http(s) URL")

    result = await db.execute(select(Course).where(Course.id == course_id, Course.user_id == user.id))
    course = result.scalar_one_or_none()
    if course is None:
        raise HTTPException(status_code=404, detail="Course not found")

    async with client:
        candidate = await resolve_outline_from_url(client, course.canvas_course_id, url)

    result = await db.execute(select(CourseOutline).where(CourseOutline.course_id == course.id))
    outline = result.scalar_one_or_none()
    if outline is None:
        outline = CourseOutline(course_id=course.id)
        db.add(outline)

    outline.source = candidate.source
    outline.title = candidate.title
    outline.canvas_url = candidate.canvas_url
    outline.external_url = candidate.external_url
    outline.confidence = candidate.confidence
    outline.fetched_content = candidate.fetched_content
    outline.detected_at = datetime.datetime.utcnow()

    await db.commit()
    await db.refresh(outline)
    return outline
