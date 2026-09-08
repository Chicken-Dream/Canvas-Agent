from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from ..canvas.client import CanvasClient
from ..canvas.sync import sync_user_data
from ..database import get_db
from ..deps import get_canvas_client_for_user, get_current_user
from ..models import User
from ..schemas import SyncResultOut

router = APIRouter(prefix="/api/sync", tags=["sync"])


@router.post("", response_model=SyncResultOut)
async def trigger_sync(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    client: CanvasClient = Depends(get_canvas_client_for_user),
) -> dict:
    async with client:
        result = await sync_user_data(db, user, client)
    return result
