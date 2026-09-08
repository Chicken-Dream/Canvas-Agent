from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .canvas.client import CanvasClient
from .config import settings
from .database import get_db
from .models import CanvasCredential, User
from .security import read_session_token


async def get_current_user(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> User:
    token = request.cookies.get(settings.session_cookie_name)
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")

    user_id = read_session_token(token)
    if user_id is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Session expired")

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")

    return user


async def get_canvas_client_for_user(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> CanvasClient:
    """Builds an authenticated Canvas API client from whatever credential
    the user logged in with (OAuth grant or Personal Access Token).

    Note: this prototype does not yet refresh expired OAuth tokens - see
    README.md "Known limitations".
    """
    result = await db.execute(select(CanvasCredential).where(CanvasCredential.user_id == user.id))
    credential = result.scalar_one_or_none()
    if credential is None:
        raise HTTPException(status_code=401, detail="No Canvas credential on file for this user")

    return CanvasClient(credential.access_token)
