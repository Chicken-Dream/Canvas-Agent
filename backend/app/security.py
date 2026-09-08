from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from .config import settings

_serializer = URLSafeTimedSerializer(settings.session_secret, salt="canvas-agent-session")

SESSION_MAX_AGE_SECONDS = 60 * 60 * 24 * 14  # 2 weeks


def create_session_token(user_id: int) -> str:
    return _serializer.dumps({"user_id": user_id})


def read_session_token(token: str) -> int | None:
    try:
        data = _serializer.loads(token, max_age=SESSION_MAX_AGE_SECONDS)
    except (BadSignature, SignatureExpired):
        return None
    return data.get("user_id")


_state_serializer = URLSafeTimedSerializer(settings.session_secret, salt="canvas-agent-oauth-state")


def create_oauth_state() -> str:
    import secrets

    return _state_serializer.dumps(secrets.token_urlsafe(16))


def verify_oauth_state(token: str, max_age: int = 600) -> bool:
    try:
        _state_serializer.loads(token, max_age=max_age)
    except (BadSignature, SignatureExpired):
        return False
    return True
