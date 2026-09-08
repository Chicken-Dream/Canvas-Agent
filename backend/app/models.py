from __future__ import annotations

import datetime

from sqlalchemy import (
    Float,
    ForeignKey,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    canvas_user_id: Mapped[int] = mapped_column(unique=True, index=True)
    name: Mapped[str] = mapped_column(String(255))
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(default=datetime.datetime.utcnow)

    credential: Mapped["CanvasCredential | None"] = relationship(
        back_populates="user", uselist=False, cascade="all, delete-orphan"
    )
    courses: Mapped[list["Course"]] = relationship(back_populates="user", cascade="all, delete-orphan")


class CanvasCredential(Base):
    """Stores whatever the user authenticated with: a real OAuth2 grant, or -
    until this app has an approved UAlberta OAuth developer key - a Canvas
    Personal Access Token pasted in by the student themselves.
    """

    __tablename__ = "canvas_credentials"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), unique=True)
    token_type: Mapped[str] = mapped_column(String(16))  # "oauth" | "pat"
    access_token: Mapped[str] = mapped_column(Text)
    refresh_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    expires_at: Mapped[datetime.datetime | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(default=datetime.datetime.utcnow)
    updated_at: Mapped[datetime.datetime] = mapped_column(
        default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow
    )

    user: Mapped["User"] = relationship(back_populates="credential")


class Course(Base):
    __tablename__ = "courses"
    __table_args__ = (UniqueConstraint("user_id", "canvas_course_id", name="uq_user_course"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    canvas_course_id: Mapped[int] = mapped_column(index=True)
    name: Mapped[str] = mapped_column(String(500))
    course_code: Mapped[str | None] = mapped_column(String(255), nullable=True)
    term: Mapped[str | None] = mapped_column(String(255), nullable=True)
    html_url: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    raw_syllabus_body: Mapped[str | None] = mapped_column(Text, nullable=True)

    # User- (and later, agent-) supplied signal for the future prioritization
    # agent. Not derived from anything Canvas exposes.
    difficulty: Mapped[int | None] = mapped_column(nullable=True)  # 1 (easy) - 5 (hard)

    # Official UAlberta course-calendar description, fetched once and
    # cached (see app/catalogue.py) - not something Canvas exposes either.
    description_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    description_url: Mapped[str | None] = mapped_column(String(500), nullable=True)

    user: Mapped["User"] = relationship(back_populates="courses")
    assignments: Mapped[list["Assignment"]] = relationship(
        back_populates="course", cascade="all, delete-orphan"
    )
    outline: Mapped["CourseOutline | None"] = relationship(
        back_populates="course", uselist=False, cascade="all, delete-orphan"
    )


class Assignment(Base):
    __tablename__ = "assignments"
    __table_args__ = (UniqueConstraint("course_id", "canvas_assignment_id", name="uq_course_assignment"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    course_id: Mapped[int] = mapped_column(ForeignKey("courses.id"), index=True)
    canvas_assignment_id: Mapped[int] = mapped_column(index=True)
    name: Mapped[str] = mapped_column(String(500))
    description_html: Mapped[str | None] = mapped_column(Text, nullable=True)
    due_at: Mapped[datetime.datetime | None] = mapped_column(nullable=True, index=True)
    points_possible: Mapped[float | None] = mapped_column(Float, nullable=True)
    html_url: Mapped[str | None] = mapped_column(String(1000), nullable=True)

    # Submission info (pulled via include[]=submission)
    submission_status: Mapped[str | None] = mapped_column(String(64), nullable=True)
    submitted_at: Mapped[datetime.datetime | None] = mapped_column(nullable=True)
    score: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Placeholder for the future prioritization agent: weight of this
    # assignment towards the final grade, when it can be resolved from the
    # course outline (e.g. "Assignment 2 - 15%").
    weight_pct: Mapped[float | None] = mapped_column(Float, nullable=True)

    course: Mapped["Course"] = relationship(back_populates="assignments")


class CourseOutline(Base):
    """Best-effort resolution of "where is the real course outline" for a
    course, since UAlberta instructors surface it very inconsistently -
    sometimes the Syllabus tab, sometimes a Modules page, sometimes a link
    that simply redirects to an external site such as a GitHub Pages site.
    """

    __tablename__ = "course_outlines"

    id: Mapped[int] = mapped_column(primary_key=True)
    course_id: Mapped[int] = mapped_column(ForeignKey("courses.id"), unique=True, index=True)

    # "syllabus_body" | "module_item" | "page" | "none"
    source: Mapped[str] = mapped_column(String(32))
    title: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # Where to send the user if they click through in Canvas itself.
    canvas_url: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    # Resolved external destination, if the outline lives off-Canvas
    # (e.g. a GitHub-hosted syllabus).
    external_url: Mapped[str | None] = mapped_column(String(1000), nullable=True)

    # 0-1 heuristic confidence that this is really the course outline.
    confidence: Mapped[float] = mapped_column(Float, default=0.0)

    # Best-effort scrape of the outline content (internal or external),
    # truncated. Feeds the future weighting/difficulty agent.
    fetched_content: Mapped[str | None] = mapped_column(Text, nullable=True)

    detected_at: Mapped[datetime.datetime] = mapped_column(default=datetime.datetime.utcnow)

    course: Mapped["Course"] = relationship(back_populates="outline")


class AgentUrgencyReport(Base):
    """The Strands agent's last urgency ranking for a user. One row per
    user - re-running the agent overwrites it rather than accumulating
    history, matching "the agent only runs again [on demand]" behavior:
    the dashboard shows whatever is stored here until the user asks for a
    fresh evaluation.
    """

    __tablename__ = "agent_urgency_reports"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), unique=True, index=True)

    # JSON-serialized UrgencyReportOut (see app/agent/urgency_agent.py).
    results_json: Mapped[str] = mapped_column(Text)
    generated_at: Mapped[datetime.datetime] = mapped_column(default=datetime.datetime.utcnow)
