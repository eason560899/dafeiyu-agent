import pytest
import httpx

from app.config import Settings
from app.provider import OpenAICompatibleProvider
from app.agent import AgentStateMachine, InvalidStateTransition
from app.models import AgentState


@pytest.mark.asyncio
async def test_mock_provider_stream_is_deterministic():
    provider = OpenAICompatibleProvider(Settings(mock_mode=True))
    chunks = [chunk async for chunk in provider.stream([{"role": "user", "content": "hello"}])]
    assert "".join(chunks) == "Mock response: hello"
    assert len(chunks) > 1


@pytest.mark.asyncio
async def test_openai_compatible_request_and_sse_are_parsed_without_leaking_key():
    seen = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["authorization"] = request.headers.get("authorization")
        if request.content and b'"stream":true' in request.content:
            return httpx.Response(200, content=(
                b'data: {"choices":[{"delta":{"content":"hi"}}]}\n\n'
                b'data: {"choices":[{"delta":{"content":"!"}}]}\n\n'
                b'data: [DONE]\n\n'))
        return httpx.Response(200, json={"model": "custom", "choices": [{"message": {"content": "hello"}}]})

    settings = Settings(mock_mode=False, provider_api_key="secret-key",
                        provider_base_url="https://provider.test", provider_model="custom")
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = OpenAICompatibleProvider(settings, client)
        result = await provider.complete([{"role": "user", "content": "hello"}])
        chunks = [chunk async for chunk in provider.stream([{"role": "user", "content": "hello"}])]
    assert result.content == "hello"
    assert "".join(chunks) == "hi!"
    assert seen == {"path": "/chat/completions", "authorization": "Bearer secret-key"}


def test_state_machine_rejects_invalid_transition():
    machine = AgentStateMachine()
    machine.transition(AgentState.THINKING)
    machine.transition(AgentState.COMPLETED)
    with pytest.raises(InvalidStateTransition):
        machine.transition(AgentState.EXECUTING)

