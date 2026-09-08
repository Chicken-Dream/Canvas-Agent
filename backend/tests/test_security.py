from __future__ import annotations

import time

import pytest
from itsdangerous import SignatureExpired

from app.security import (
    create_oauth_state,
    create_session_token,
    read_session_token,
    verify_oauth_state,
)


def _tamper(s: str) -> str:
    # Flipping the very last base64 character can occasionally decode to
    # the same bytes (unused padding bits), which wouldn't actually test
    # signature verification - flip one in the middle instead.
    i = len(s) // 2
    return s[:i] + ("A" if s[i] != "A" else "B") + s[i + 1 :]


def test_session_token_round_trip():
    token = create_session_token(42)
    assert read_session_token(token) == 42


def test_session_token_rejects_tampered_signature():
    token = create_session_token(42)
    assert read_session_token(_tamper(token)) is None


def test_session_token_rejects_garbage():
    assert read_session_token("not-a-real-token") is None


def test_session_token_expires_after_its_max_age():
    """read_session_token() itself is only ever exercised against the real
    2-week SESSION_MAX_AGE_SECONDS window, so hit the same serializer
    directly with a short window to prove expiry is actually enforced.
    """
    from app import security as security_mod

    token = security_mod._serializer.dumps({"user_id": 42})
    time.sleep(2)
    with pytest.raises(SignatureExpired):
        security_mod._serializer.loads(token, max_age=1)


def test_oauth_state_round_trip():
    state = create_oauth_state()
    assert verify_oauth_state(state) is True


def test_oauth_state_rejects_tampered_signature():
    state = create_oauth_state()
    assert verify_oauth_state(_tamper(state)) is False


def test_oauth_state_rejects_garbage():
    assert verify_oauth_state("not-a-real-state") is False


def test_oauth_state_rejects_expired():
    state = create_oauth_state()
    # itsdangerous timestamps have 1-second resolution, so sleeping just
    # over the max_age boundary can round back under it; sleep well past
    # it instead of chasing that edge.
    time.sleep(2)
    assert verify_oauth_state(state, max_age=1) is False


def test_session_and_oauth_state_use_independent_salts():
    """A session token must not verify as a valid OAuth state and vice
    versa, even though both are signed with the same SESSION_SECRET -
    the distinct salts are what keep them from being interchangeable.
    """
    token = create_session_token(42)
    assert verify_oauth_state(token) is False
