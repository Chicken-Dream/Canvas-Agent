from __future__ import annotations

import datetime
import json

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..agent.urgency_agent import run_urgency_agent
from ..database import get_db
from ..deps import get_current_user
from ..models import AgentUrgencyReport, User
from ..schemas import UrgencyReportOut

router = APIRouter(prefix="/api/agent", tags=["agent"])


@router.get("/urgency", response_model=UrgencyReportOut | None)
async def get_urgency_report(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> UrgencyReportOut | None:
    """The agent's last stored ranking for this user, or null if it has
    never been run. This never triggers a new agent run - that only
    happens via POST /urgency/evaluate (the dashboard's button).
    """
    result = await db.execute(select(AgentUrgencyReport).where(AgentUrgencyReport.user_id == user.id))
    report = result.scalar_one_or_none()
    if report is None:
        return None
    return UrgencyReportOut(generated_at=report.generated_at, **json.loads(report.results_json))


@router.post("/urgency/evaluate", response_model=UrgencyReportOut)
async def evaluate_urgency(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> UrgencyReportOut:
    """Runs the Strands urgency agent and overwrites the stored result for
    this user. This is the only thing that runs the agent - it's not run
    automatically on page load or on a schedule.
    """
    try:
        report_out = await run_urgency_agent(db, user)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Agent run failed: {exc}") from exc

    now = datetime.datetime.utcnow()
    result = await db.execute(select(AgentUrgencyReport).where(AgentUrgencyReport.user_id == user.id))
    row = result.scalar_one_or_none()
    if row is None:
        row = AgentUrgencyReport(user_id=user.id)
        db.add(row)

    row.results_json = report_out.model_dump_json()
    row.generated_at = now
    await db.commit()

    return UrgencyReportOut(generated_at=now, **report_out.model_dump())
