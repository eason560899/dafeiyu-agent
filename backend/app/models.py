from datetime import datetime, timezone
from enum import StrEnum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field, ConfigDict


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class AgentState(StrEnum):
    IDLE = "idle"
    THINKING = "thinking"
    EXECUTING = "executing"
    WAITING_CONFIRMATION = "waiting_confirmation"
    COMPLETED = "completed"
    ERROR = "error"


class ChatMessage(BaseModel):
    role: str = Field(pattern="^(system|user|assistant|tool)$")
    content: str = Field(min_length=1, max_length=100_000)


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=100_000)
    conversation_id: str | None = None
    context: dict[str, Any] = Field(default_factory=dict)
    stream: bool = False


class Candidate(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    conversation_id: str
    content: str
    provider: str = "mock"
    model: str = "mock"
    score: float | None = None
    selected: bool = False
    created_at: str = Field(default_factory=now_iso)
    # Optional discovery fields. Chat responses can keep using ``content`` while
    # source adapters attach platform-specific identity and links.
    platform: str | None = None
    content_id: str | None = None
    title: str | None = None
    summary: str | None = None
    url: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class Action(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    conversation_id: str
    type: str
    payload: dict[str, Any] = Field(default_factory=dict)
    status: str = "pending"
    created_at: str = Field(default_factory=now_iso)
    idempotency_key: str | None = None


class ActionDraft(BaseModel):
    """A proposed external side effect before it is executed."""

    conversation_id: str
    platform: str
    type: str
    payload: dict[str, Any] = Field(default_factory=dict)
    idempotency_key: str | None = None


class ActionResult(BaseModel):
    ok: bool
    status: str
    action_id: str
    platform: str | None = None
    detail: str | None = None
    result: dict[str, Any] = Field(default_factory=dict)


class Event(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    conversation_id: str
    type: str
    state: AgentState
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: str = Field(default_factory=now_iso)


class Connection(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    name: str
    kind: str
    config: dict[str, Any] = Field(default_factory=dict)
    status: str = "disconnected"
    created_at: str = Field(default_factory=now_iso)


class ChatResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    conversation_id: str
    message: str
    state: AgentState
    candidates: list[Candidate] = Field(default_factory=list)
    events: list[Event] = Field(default_factory=list)

