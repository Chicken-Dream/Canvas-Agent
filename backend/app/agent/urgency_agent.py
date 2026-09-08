from __future__ import annotations

import boto3
from botocore.exceptions import ClientError
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from strands import Agent, ModelRetryStrategy, tool
from strands.models.bedrock import BedrockModel

from ..config import settings
from ..models import User
from .tools import (
    get_course_catalogue_description,
    get_courses_context_json,
    get_upcoming_assignments_json,
)

# Bedrock intermittently returns ModelErrorException (a transient
# upstream-model-side failure, HTTP 424) - observed in practice on
# amazon.nova-micro-v1:0 under normal use, not tied to any account/access
# issue (the same request succeeds on retry). Strands' default retry
# strategy only covers ModelThrottledException, so it doesn't retry this.
_RETRYABLE_BEDROCK_ERROR_CODES = {
    "ModelErrorException",
    "ModelTimeoutException",
    "ServiceUnavailableException",
    "ThrottlingException",
    "InternalServerException",
}


class _BedrockTransientErrorRetryStrategy(ModelRetryStrategy):
    def is_retryable(self, exception: Exception) -> bool:
        if super().is_retryable(exception):
            return True
        if isinstance(exception, ClientError):
            code = exception.response.get("Error", {}).get("Code", "")
            return code in _RETRYABLE_BEDROCK_ERROR_CODES
        return False

SYSTEM_PROMPT = """You are a study-prioritization assistant for a University \
of Alberta student. You have three tools:

- get_upcoming_assignments: assignments due in the next 14 days, JSON, most \
  recently-due-in-the-future first.
- get_courses: each course's subjective difficulty (1=easy, 5=hard, set by \
  the student) and any course outline text that was found (grading \
  weights, exam policies, etc).
- get_course_description(subject, number): the official UAlberta calendar \
  description for a course, e.g. subject="cmput", number="379". Derive \
  subject/number from a course's course_code.

Call get_upcoming_assignments and get_courses first. Use \
get_course_description for courses whose assignments you are actually \
considering, to judge how conceptually demanding the material is.

Rank urgency using all of: how soon the assignment is due, the course's \
subjective difficulty rating, the assignment's weight toward the final \
grade if you can determine it from the outline text (assignments with an \
explicit stated percentage should generally outrank ones without, all else \
equal), and how demanding the course's subject matter is per its \
catalogue description.

Return the 5 most urgent assignments, most urgent first. For each, write \
exactly one sentence explaining why it made the top 5 - reference the \
specific factor(s) that matter most for that assignment (e.g. "due in 2 \
days and worth 20% of your grade" or "the course is rated difficulty 5/5 \
and this covers particularly dense material per the outline"). If fewer \
than 5 assignments exist in the 14-day window, return however many there \
are."""


class UrgentAssignmentOut(BaseModel):
    assignment_id: int
    name: str
    course_code: str | None = None
    due_at: str = Field(description="ISO 8601 due date/time")
    reason: str = Field(description="One sentence explaining why this assignment is urgent")


class UrgencyReportOut(BaseModel):
    top_assignments: list[UrgentAssignmentOut]


def _build_tools(db: AsyncSession, user: User) -> list:
    """Strands tools as closures over this request's db/user - the LLM
    never sees or controls those, only the arguments declared below.
    """

    @tool
    async def get_upcoming_assignments() -> str:
        """Assignments due in the next 14 days, as JSON, most-future-due-date first."""
        return await get_upcoming_assignments_json(db, user, days=14)

    @tool
    async def get_courses() -> str:
        """Each course's subjective difficulty and resolved outline text, as JSON."""
        return await get_courses_context_json(db, user)

    @tool
    async def get_course_description(subject: str, number: str) -> str:
        """Official UAlberta calendar description for a course (e.g. subject="cmput", number="379")."""
        return await get_course_catalogue_description(subject, number)

    return [get_upcoming_assignments, get_courses, get_course_description]


def _build_model() -> BedrockModel:
    session = boto3.Session(
        aws_access_key_id=settings.aws_access_key_id or None,
        aws_secret_access_key=settings.aws_secret_access_key or None,
        region_name=settings.aws_region,
    )
    # Non-streaming: this endpoint returns one final result over plain
    # HTTP, not a live token stream, so there's nothing to gain from
    # ConverseStream here.
    return BedrockModel(
        boto_session=session,
        model_id=settings.bedrock_model_id,
        temperature=0.2,
        streaming=False,
    )


async def run_urgency_agent(db: AsyncSession, user: User) -> UrgencyReportOut:
    if not settings.aws_access_key_id or not settings.aws_secret_access_key:
        raise RuntimeError(
            "AWS Bedrock credentials are not configured (AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY)."
        )

    agent = Agent(
        model=_build_model(),
        tools=_build_tools(db, user),
        system_prompt=SYSTEM_PROMPT,
        retry_strategy=_BedrockTransientErrorRetryStrategy(max_attempts=3, initial_delay=2, max_delay=10),
        # Strands' default callback handler prints the model's full
        # reasoning/tool-call trace to stdout on every request - fine for a
        # one-off script, not for a web server whose stdout becomes
        # CloudWatch Logs (cost, noise, and it's someone's assignment data).
        callback_handler=None,
    )

    result = await agent.invoke_async(
        "Evaluate my upcoming assignments and return the 5 most urgent, most urgent first.",
        structured_output_model=UrgencyReportOut,
    )
    if result.structured_output is None:
        raise RuntimeError("Agent did not return structured output.")
    return result.structured_output
