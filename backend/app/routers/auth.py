from __future__ import annotations

import datetime

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import RedirectResponse

from ..canvas.client import CanvasAPIError, CanvasClient, canvas_oauth_authorize_url, exchange_code_for_token
from ..config import settings
from ..database import get_db
from ..deps import get_current_user
from ..models import CanvasCredential, User
from ..schemas import DevLoginIn, UserOut
from ..security import create_oauth_state, create_session_token, verify_oauth_state

router = APIRouter(prefix="/auth", tags=["auth"])

_COOKIE_KWARGS = dict(
    httponly=True,
    samesite=settings.session_cookie_samesite,
    secure=settings.session_cookie_secure,
    path="/",
)


async def _upsert_user_and_credential(
    db: AsyncSession,
    profile: dict,
    token_type: str,
    access_token: str,
    refresh_token: str | None,
    expires_at: datetime.datetime | None,
) -> User:
    result = await db.execute(select(User).where(User.canvas_user_id == profile["id"]))
    user = result.scalar_one_or_none()
    if user is None:
        user = User(
            canvas_user_id=profile["id"],
            name=profile.get("name", "Canvas User"),
            email=profile.get("primary_email") or profile.get("email"),
        )
        db.add(user)
        await db.flush()
    else:
        user.name = profile.get("name", user.name)

    result = await db.execute(select(CanvasCredential).where(CanvasCredential.user_id == user.id))
    credential = result.scalar_one_or_none()
    if credential is None:
        credential = CanvasCredential(user_id=user.id)
        db.add(credential)

    credential.token_type = token_type
    credential.access_token = access_token
    credential.refresh_token = refresh_token
    credential.expires_at = expires_at

    await db.commit()
    await db.refresh(user)
    return user


@router.get("/canvas/login")
async def canvas_login() -> RedirectResponse:
    if not settings.canvas_client_id:
        raise HTTPException(
            status_code=503,
            detail=(
                "Canvas OAuth is not configured (CANVAS_CLIENT_ID missing). "
                "This requires a Developer Key issued by UAlberta's Canvas admins. "
                "Use POST /auth/dev-login with a Personal Access Token in the meantime."
            ),
        )
    state = create_oauth_state()
    resp = RedirectResponse(canvas_oauth_authorize_url(state))
    resp.set_cookie("canvas_oauth_state", state, max_age=600, **_COOKIE_KWARGS)
    return resp


@router.get("/canvas/callback")
async def canvas_callback(
    request: Request,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    db: AsyncSession = Depends(get_db),
) -> RedirectResponse:
    if error:
        raise HTTPException(status_code=400, detail=f"Canvas OAuth error: {error}")

    cookie_state = request.cookies.get("canvas_oauth_state")
    if not code or not state or not cookie_state or state != cookie_state or not verify_oauth_state(state):
        raise HTTPException(status_code=400, detail="Invalid or expired OAuth state")

    try:
        token_data = await exchange_code_for_token(code)
    except CanvasAPIError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    access_token = token_data["access_token"]
    refresh_token = token_data.get("refresh_token")
    expires_in = token_data.get("expires_in")
    expires_at = (
        datetime.datetime.utcnow() + datetime.timedelta(seconds=expires_in) if expires_in else None
    )

    profile = token_data.get("user") or {}
    if "id" not in profile:
        async with CanvasClient(access_token) as client:
            profile = await client.get_self()

    user = await _upsert_user_and_credential(db, profile, "oauth", access_token, refresh_token, expires_at)

    resp = RedirectResponse(settings.frontend_origin)
    resp.delete_cookie("canvas_oauth_state", path="/")
    resp.set_cookie(settings.session_cookie_name, create_session_token(user.id), max_age=60 * 60 * 24 * 14, **_COOKIE_KWARGS)
    return resp


@router.post("/dev-login", response_model=UserOut)
async def dev_login(payload: DevLoginIn, response: Response, db: AsyncSession = Depends(get_db)) -> User:
    """Prototype-only login path: the student pastes a Canvas Personal
    Access Token (Account -> Settings -> + New Access Token on
    canvas.ualberta.ca) instead of going through OAuth. Real institutional
    OAuth requires a Developer Key from UAlberta's Canvas admins - see
    README.md.
    """
    async with CanvasClient(payload.access_token) as client:
        try:
            profile = await client.get_self()
        except CanvasAPIError as exc:
            raise HTTPException(status_code=401, detail="Canvas rejected this access token") from exc

    user = await _upsert_user_and_credential(db, profile, "pat", payload.access_token, None, None)
    response.set_cookie(settings.session_cookie_name, create_session_token(user.id), max_age=60 * 60 * 24 * 14, **_COOKIE_KWARGS)
    return user


@router.post("/logout")
async def logout(response: Response) -> dict:
    response.delete_cookie(settings.session_cookie_name, path="/")
    return {"ok": True}


@router.get("/me", response_model=UserOut)
async def me(user: User = Depends(get_current_user)) -> User:
    return user
