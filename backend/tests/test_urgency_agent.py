from __future__ import annotations

import pytest
from botocore.exceptions import ClientError
from strands.types.exceptions import ModelThrottledException

import app.agent.urgency_agent as urgency_agent_mod
from app.agent.urgency_agent import (
    UrgencyReportOut,
    UrgentAssignmentOut,
    _BedrockTransientErrorRetryStrategy,
    run_urgency_agent,
)
from app.config import settings
from app.models import User


def _client_error(code: str) -> ClientError:
    return ClientError({"Error": {"Code": code, "Message": "boom"}}, "Converse")


class TestBedrockTransientErrorRetryStrategy:
    def setup_method(self):
        self.strategy = _BedrockTransientErrorRetryStrategy()

    def test_retries_model_error_exception(self):
        assert self.strategy.is_retryable(_client_error("ModelErrorException")) is True

    def test_retries_throttling_and_service_errors(self):
        assert self.strategy.is_retryable(_client_error("ThrottlingException")) is True
        assert self.strategy.is_retryable(_client_error("ServiceUnavailableException")) is True
        assert self.strategy.is_retryable(_client_error("InternalServerException")) is True
        assert self.strategy.is_retryable(_client_error("ModelTimeoutException")) is True

    def test_does_not_retry_non_transient_client_errors(self):
        assert self.strategy.is_retryable(_client_error("AccessDeniedException")) is False
        assert self.strategy.is_retryable(_client_error("ResourceNotFoundException")) is False

    def test_still_retries_strands_own_throttled_exception(self):
        assert self.strategy.is_retryable(ModelThrottledException("throttled")) is True

    def test_does_not_retry_unrelated_exceptions(self):
        assert self.strategy.is_retryable(ValueError("not a client error")) is False


class FakeAgentResult:
    def __init__(self, structured_output):
        self.structured_output = structured_output


class FakeAgent:
    """Stands in for strands.Agent - records how it was constructed/called
    without making any real Bedrock call.
    """

    last_instance: "FakeAgent | None" = None

    def __init__(self, model=None, tools=None, system_prompt=None, retry_strategy=None, callback_handler=False):
        self.model = model
        self.tools = tools
        self.system_prompt = system_prompt
        self.retry_strategy = retry_strategy
        self.callback_handler = callback_handler
        self.invoke_calls = []
        FakeAgent.last_instance = self

    async def invoke_async(self, prompt, *, structured_output_model=None, **kwargs):
        self.invoke_calls.append({"prompt": prompt, "structured_output_model": structured_output_model})
        return FakeAgentResult(
            UrgencyReportOut(
                top_assignments=[
                    UrgentAssignmentOut(
                        assignment_id=1,
                        name="Fake Assignment",
                        course_code="TEST 101",
                        due_at="2026-09-10T00:00:00Z",
                        reason="Because the fake agent said so",
                    )
                ]
            )
        )


@pytest.fixture(autouse=True)
def aws_credentials_configured(monkeypatch):
    monkeypatch.setattr(settings, "aws_access_key_id", "fake-key-id")
    monkeypatch.setattr(settings, "aws_secret_access_key", "fake-secret")


async def test_run_urgency_agent_raises_without_credentials(monkeypatch, db_session):
    monkeypatch.setattr(settings, "aws_access_key_id", "")
    monkeypatch.setattr(settings, "aws_secret_access_key", "")
    # Force boto3's own credential chain to genuinely find nothing, rather
    # than relying on the test environment happening to have no ambient
    # AWS credentials (env vars, ~/.aws/credentials, instance metadata).
    monkeypatch.setattr("boto3.Session.get_credentials", lambda self: None)
    user = User(canvas_user_id=1, name="Test Student")
    db_session.add(user)
    await db_session.flush()

    with pytest.raises(RuntimeError, match="No AWS credentials"):
        await run_urgency_agent(db_session, user)


async def test_run_urgency_agent_works_without_static_keys_via_instance_role(monkeypatch, db_session):
    """The bug this guards against: a deployment with no
    AWS_ACCESS_KEY_ID/SECRET but a valid credential source elsewhere (an
    EC2 instance role, in production) must not be rejected - only genuinely
    unresolvable credentials should raise. See AWS_SETUP.md.
    """
    monkeypatch.setattr(settings, "aws_access_key_id", "")
    monkeypatch.setattr(settings, "aws_secret_access_key", "")
    monkeypatch.setattr("boto3.Session.get_credentials", lambda self: object())
    monkeypatch.setattr(urgency_agent_mod, "Agent", FakeAgent)

    user = User(canvas_user_id=4, name="Test Student 4")
    db_session.add(user)
    await db_session.flush()

    result = await run_urgency_agent(db_session, user)

    assert isinstance(result, UrgencyReportOut)


async def test_run_urgency_agent_wires_tools_and_returns_structured_output(monkeypatch, db_session):
    monkeypatch.setattr(urgency_agent_mod, "Agent", FakeAgent)
    user = User(canvas_user_id=2, name="Test Student 2")
    db_session.add(user)
    await db_session.flush()

    result = await run_urgency_agent(db_session, user)

    assert isinstance(result, UrgencyReportOut)
    assert result.top_assignments[0].name == "Fake Assignment"

    agent = FakeAgent.last_instance
    assert agent is not None
    tool_names = {t.tool_name for t in agent.tools}
    assert tool_names == {"get_upcoming_assignments", "get_courses", "get_course_description"}
    assert isinstance(agent.retry_strategy, _BedrockTransientErrorRetryStrategy)
    # Explicitly disabled, not just left at Strands' printing-to-stdout default.
    assert agent.callback_handler is None
    assert agent.invoke_calls[0]["structured_output_model"] is UrgencyReportOut


async def test_run_urgency_agent_raises_when_no_structured_output(monkeypatch, db_session):
    class NoOutputAgent(FakeAgent):
        async def invoke_async(self, prompt, *, structured_output_model=None, **kwargs):
            return FakeAgentResult(None)

    monkeypatch.setattr(urgency_agent_mod, "Agent", NoOutputAgent)
    user = User(canvas_user_id=3, name="Test Student 3")
    db_session.add(user)
    await db_session.flush()

    with pytest.raises(RuntimeError, match="did not return structured output"):
        await run_urgency_agent(db_session, user)
