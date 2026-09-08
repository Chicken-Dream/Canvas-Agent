from __future__ import annotations

import datetime

from pydantic import BaseModel, ConfigDict


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    canvas_user_id: int
    name: str
    email: str | None = None


class CourseOutlineOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    source: str
    title: str | None
    canvas_url: str | None
    external_url: str | None
    confidence: float
    detected_at: datetime.datetime


class CourseOutlineDetailOut(CourseOutlineOut):
    fetched_content: str | None


class AssignmentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    canvas_assignment_id: int
    name: str
    due_at: datetime.datetime | None
    points_possible: float | None
    html_url: str | None
    submission_status: str | None
    submitted_at: datetime.datetime | None
    score: float | None
    weight_pct: float | None


class AssignmentWithCourseOut(AssignmentOut):
    course_id: int
    course_name: str
    course_code: str | None


class CourseOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    canvas_course_id: int
    name: str
    course_code: str | None
    term: str | None
    html_url: str | None
    difficulty: int | None
    outline: CourseOutlineOut | None = None


class CourseDetailOut(CourseOut):
    assignments: list[AssignmentOut] = []
    outline: CourseOutlineDetailOut | None = None
    description_text: str | None = None
    description_url: str | None = None


class CourseUpdateIn(BaseModel):
    difficulty: int | None = None


class OutlineSubmitIn(BaseModel):
    url: str


class DevLoginIn(BaseModel):
    access_token: str


class SyncResultOut(BaseModel):
    courses: int
    assignments: int
    outlines_found: int


class UrgentAssignmentOut(BaseModel):
    assignment_id: int
    name: str
    course_code: str | None = None
    due_at: str
    reason: str


class UrgencyReportOut(BaseModel):
    generated_at: datetime.datetime
    top_assignments: list[UrgentAssignmentOut]
