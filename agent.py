from typing import Any, AsyncIterator
from uuid import uuid4

from .models import AgentState, Candidate, Event
from .provider import OpenAICompatibleProvider
from .storage import SQLiteStore


class InvalidStateTransition(ValueError):
    pass


class AgentStateMachine:
    """Small explicit transition guard used by integrations and tests."""

    transitions: dict[AgentState, set[AgentState]] = {
        AgentState.IDLE: {AgentState.THINKING, AgentState.ERROR},
        AgentState.THINKING: {AgentState.EXECUTING, AgentState.COMPLETED, AgentState.ERROR,
                              AgentState.WAITING_CONFIRMATION},
        AgentState.EXECUTING: {AgentState.THINKING, AgentState.COMPLETED, AgentState.ERROR,
                               AgentState.WAITING_CONFIRMATION},
        AgentState.WAITING_CONFIRMATION: {AgentState.EXECUTING, AgentState.COMPLETED, AgentState.ERROR},
        AgentState.COMPLETED: {AgentState.IDLE, AgentState.THINKING},
        AgentState.ERROR: {AgentState.IDLE, AgentState.THINKING},
    }

    def __init__(self, state: AgentState = AgentState.IDLE):
        self.state = state

    def transition(self, state: AgentState) -> AgentState:
        if state not in self.transitions[self.state]:
            raise InvalidStateTransition(f"Cannot transition from {self.state.value} to {state.value}")
        self.state = state
        return self.state


class Agent:
    """Minimal deterministic state machine around a chat provider."""

    def __init__(self, store: SQLiteStore, provider: OpenAICompatibleProvider):
        self.store = store
        self.provider = provider

    def _event(self, conversation_id: str, event_type: str, state: AgentState, payload: dict[str, Any] | None = None) -> Event:
        event = Event(id=str(uuid4()), conversation_id=conversation_id, type=event_type,
                      state=state, payload=payload or {})
        return self.store.add_event(event)

    async def run(self, conversation_id: str, message: str, context: dict[str, Any] | None = None) -> tuple[Candidate, list[Event]]:
        self.store.ensure_conversation(conversation_id)
        self.store.add_message(conversation_id, "user", message)
        events = [self._event(conversation_id, "run_started", AgentState.THINKING)]
        try:
            messages = self.store.get_messages(conversation_id)
            result = await self.provider.complete(messages, context)
            self.store.add_message(conversation_id, "assistant", result.content)
            candidate = self.store.add_candidate(Candidate(
                conversation_id=conversation_id, content=result.content,
                provider=result.provider, model=result.model, selected=True,
            ))
            events.append(self._event(conversation_id, "response_created", AgentState.COMPLETED,
                                      {"candidate_id": candidate.id}))
            return candidate, events
        except Exception as exc:
            events.append(self._event(conversation_id, "run_failed", AgentState.ERROR,
                                      {"error": str(exc)}))
            raise

    async def run_stream(self, conversation_id: str, message: str,
                         context: dict[str, Any] | None = None) -> AsyncIterator[tuple[str, Any]]:
        """Stream provider deltas while preserving the same durable state as ``run``."""
        self.store.ensure_conversation(conversation_id)
        self.store.add_message(conversation_id, "user", message)
        started = self._event(conversation_id, "run_started", AgentState.THINKING)
        yield "event", started
        text_parts: list[str] = []
        try:
            messages = self.store.get_messages(conversation_id)
            async for delta in self.provider.stream(messages, context):
                text_parts.append(delta)
                yield "delta", delta
            content = "".join(text_parts)
            self.store.add_message(conversation_id, "assistant", content)
            candidate = self.store.add_candidate(Candidate(
                conversation_id=conversation_id, content=content,
                provider="mock" if self.provider.settings.mock_mode else "openai-compatible",
                model="mock" if self.provider.settings.mock_mode else self.provider.settings.provider_model,
                selected=True,
            ))
            completed = self._event(conversation_id, "response_created", AgentState.COMPLETED,
                                    {"candidate_id": candidate.id})
            yield "event", completed
            yield "complete", candidate
        except Exception as exc:
            failed = self._event(conversation_id, "run_failed", AgentState.ERROR, {"error": str(exc)})
            yield "event", failed
            raise

