import asyncio
import json
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
import re
import secrets
from typing import Any
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .agent import Agent
from .config import Settings, get_settings
from .models import Action, ActionResult, AgentState, ChatRequest, ChatResponse, Connection, Event
from .provider import OpenAICompatibleProvider
from .storage import SQLiteStore
from .connectors import ADAPTERS, adapter_for


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    store = SQLiteStore(settings.database_url)
    provider = OpenAICompatibleProvider(settings)
    agent = Agent(store, provider)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        yield
        store.close()

    app = FastAPI(title="DaFeiYu Agent API", version="0.1.0", lifespan=lifespan)
    app.state.store = store
    app.state.provider = provider
    app.state.agent = agent
    app.state.automation = {
        "enabled": settings.automation_enabled,
        "paused_platforms": set(),
        "last_action_at": {},
        "daily_counts": {},
        "min_interval_seconds": 3.0,
        "daily_limit": 100,
    }
    app.state.discovery_config = {"enabled": False, "schedule": "", "sources": [], "prompt": ""}
    app.state.device_tokens = {settings.device_token} if settings.device_token else set()
    app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins,
                       allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

    @app.middleware("http")
    async def device_auth(request: Request, call_next):
        """Optional LAN/Tailscale device gate.

        A blank token is intentionally allowed for first-run localhost setup.
        Once DAFEIYU_DEVICE_TOKEN is configured, API keys and connector secrets
        are never accepted as a substitute for this short-lived device token.
        """
        public_paths = {"/api/health", "/api/auth/device/pair", "/api/auth/device"}
        if request.url.path.startswith("/api/") and request.url.path not in public_paths and app.state.device_tokens:
            supplied = request.headers.get("x-device-token")
            if supplied not in app.state.device_tokens:
                return JSONResponse({"detail": "Invalid device token"}, status_code=401)
        return await call_next(request)

    class DevicePairRequest(BaseModel):
        code: str = Field(min_length=1, max_length=200)
        device_token: str | None = None
        device_name: str | None = Field(default=None, max_length=100)

    @app.post("/api/auth/device/pair")
    def pair_device(request: DevicePairRequest):
        expected = settings.device_token
        # Pairing is intentionally a local bootstrap: deployments should set a
        # token in .env and enter that one-time code in the PWA. We never echo a
        # provider/API secret here.
        if expected and request.code != expected and request.device_token != expected:
            raise HTTPException(status_code=401, detail="Invalid pairing code")
        if not expected and request.code.lower() != "local":
            raise HTTPException(status_code=401, detail="Set DAFEIYU_DEVICE_TOKEN or use local bootstrap code")
        token = expected or request.device_token or secrets.token_urlsafe(24)
        app.state.device_tokens.add(token)
        return {"token": token, "device": {"name": request.device_name or "device"}}

    @app.get("/api/auth/device")
    def device_status(request: Request):
        token = request.headers.get("x-device-token")
        return {"paired": bool(token and token in app.state.device_tokens),
                "device_name": "local device"}

    @app.get("/health")
    @app.get("/api/health")
    def health() -> dict[str, Any]:
        return {"status": "ok", "service": "agent-workspace", "mock_mode": settings.mock_mode,
                "automation_enabled": app.state.automation["enabled"],
                "uptime": "—", "latency": "local", "queue": 0, "updatedAt": "刚刚"}

    @app.post("/api/chat", response_model=ChatResponse)
    async def chat(request: ChatRequest) -> ChatResponse:
        conversation_id = request.conversation_id or str(uuid4())
        try:
            candidate, events = await agent.run(conversation_id, request.message, request.context)
        except Exception as exc:
            raise HTTPException(status_code=502, detail="Provider request failed") from exc
        return ChatResponse(conversation_id=conversation_id, message=candidate.content,
                            state=AgentState.COMPLETED, candidates=[candidate], events=events)

    @app.post("/api/chat/stream")
    async def chat_stream(request: ChatRequest):
        """Return newline-delimited JSON events for clients that prefer streaming."""
        import json
        conversation_id = request.conversation_id or str(uuid4())

        async def generate():
            try:
                async for kind, item in agent.run_stream(conversation_id, request.message, request.context):
                    if kind == "delta":
                        yield json.dumps({"type": "delta", "data": item}, ensure_ascii=False) + "\n"
                    elif kind == "event":
                        yield json.dumps({"type": "event", "data": item.model_dump()}, ensure_ascii=False) + "\n"
                    elif kind == "complete":
                        yield json.dumps({"type": "message", "data": {"conversation_id": conversation_id,
                                                                           "message": item.content,
                                                                           "state": AgentState.COMPLETED.value}}, ensure_ascii=False) + "\n"
            except Exception:
                yield json.dumps({"type": "error", "data": {"message": "Provider request failed"}}) + "\n"

        return StreamingResponse(generate(), media_type="application/x-ndjson")

    @app.get("/api/providers")
    def providers():
        return [{"id": "mock", "name": "Mock provider", "model": "mock", "available": True,
                 "status": "active" if settings.mock_mode else "standby"},
                {"id": "openai-compatible", "name": "OpenAI compatible", "model": settings.provider_model,
                 "endpoint": settings.provider_base_url,
                 "available": bool(settings.provider_api_key) and not settings.mock_mode,
                 "status": "active" if settings.provider_api_key and not settings.mock_mode else "inactive"}]

    class ProviderRequest(BaseModel):
        name: str = "OpenAI compatible"
        endpoint: str | None = None
        model: str | None = None
        key: str | None = None

    @app.post("/api/providers")
    def save_provider(request: ProviderRequest):
        # Keys are accepted only by the local process and never included in the response.
        # Persisting credentials is intentionally left to the host environment/keyring.
        if request.endpoint:
            settings.provider_base_url = request.endpoint.rstrip("/")
        if request.model:
            settings.provider_model = request.model
        if request.key and not request.key.startswith("sk-••"):
            settings.provider_api_key = request.key
            settings.mock_mode = False
        return {"id": "openai-compatible", "name": request.name,
                "endpoint": settings.provider_base_url,
                "model": settings.provider_model,
                "status": "active" if settings.provider_api_key and not settings.mock_mode else "mock"}

    @app.get("/api/candidates")
    def candidates(conversation_id: str | None = None, limit: int = Query(100, ge=1, le=500)):
        return store.list_candidates(conversation_id, limit)

    class TaskConfigRequest(BaseModel):
        enabled: bool = False
        schedule: str = Field(default="", max_length=200)
        sources: list[str] = Field(default_factory=list, max_length=20)
        prompt: str = Field(default="", max_length=10_000)

    @app.get("/api/tasks/config")
    def task_config():
        return app.state.discovery_config

    @app.put("/api/tasks/config")
    def save_task_config(request: TaskConfigRequest):
        app.state.discovery_config = request.model_dump()
        return app.state.discovery_config

    @app.post("/api/candidates/{candidate_id}/approve")
    def approve_candidate(candidate_id: str):
        candidate = store.update_candidate_selected(candidate_id, True)
        if candidate is None:
            raise HTTPException(status_code=404, detail="Candidate not found")
        return candidate

    @app.get("/api/actions")
    def actions(conversation_id: str | None = None, limit: int = Query(100, ge=1, le=500)):
        return store.list_actions(conversation_id, limit)

    class ActionRequest(BaseModel):
        conversation_id: str
        type: str = Field(min_length=1, max_length=100)
        platform: str | None = None
        payload: dict[str, Any] = {}
        status: str = "pending"
        idempotency_key: str | None = Field(default=None, max_length=200)

    @app.post("/api/actions", response_model=Action)
    def create_action(request: ActionRequest):
        key = request.idempotency_key or request.payload.get("idempotency_key")
        if key:
            existing = store.find_action_by_idempotency(key)
            if existing is not None:
                return existing
        payload = dict(request.payload)
        if request.platform:
            payload.setdefault("platform", request.platform)
        return store.add_action(Action(conversation_id=request.conversation_id, type=request.type,
                                       payload=payload, status=request.status, idempotency_key=key))

    @app.post("/api/actions/{action_id}/approve", response_model=Action)
    def approve_action(action_id: str):
        action = store.update_action_status(action_id, "approved")
        if action is None:
            raise HTTPException(status_code=404, detail="Action not found")
        return action

    @app.post("/api/actions/{action_id}/reject", response_model=Action)
    def reject_action(action_id: str):
        action = store.update_action_status(action_id, "rejected")
        if action is None:
            raise HTTPException(status_code=404, detail="Action not found")
        return action

    @app.post("/api/actions/{action_id}/execute", response_model=ActionResult)
    async def execute_action(action_id: str):
        action = next((item for item in store.list_actions(limit=500) if item.id == action_id), None)
        if action is None:
            raise HTTPException(status_code=404, detail="Action not found")
        platform = str(action.payload.get("platform") or action.payload.get("kind") or "").lower()
        if not app.state.automation["enabled"]:
            raise HTTPException(status_code=423, detail="Automation is stopped")
        if platform in app.state.automation["paused_platforms"]:
            raise HTTPException(status_code=423, detail="Platform automation is paused")
        if not platform:
            raise HTTPException(status_code=422, detail="Action payload requires platform")
        now = datetime.now(timezone.utc)
        last = app.state.automation["last_action_at"].get(platform)
        if last is not None and (now - last).total_seconds() < app.state.automation["min_interval_seconds"]:
            app.state.automation["paused_platforms"].add(platform)
            store.update_action_status(action_id, "paused")
            store.add_event(Event(conversation_id=action.conversation_id, type="action_paused",
                                  state=AgentState.ERROR, payload={"action_id": action_id, "platform": platform,
                                                                    "reason": "send_interval"}))
            raise HTTPException(status_code=429, detail="Platform send interval limit reached; automation paused")
        day = now.date().isoformat()
        count_key = f"{platform}:{day}"
        count = app.state.automation["daily_counts"].get(count_key, 0)
        if count >= app.state.automation["daily_limit"]:
            app.state.automation["paused_platforms"].add(platform)
            store.update_action_status(action_id, "paused")
            store.add_event(Event(conversation_id=action.conversation_id, type="action_paused",
                                  state=AgentState.ERROR, payload={"action_id": action_id, "platform": platform,
                                                                    "reason": "daily_limit"}))
            raise HTTPException(status_code=429, detail="Platform daily limit reached; automation paused")
        try:
            adapter = adapter_for(platform, action.payload.get("connection_config") or action.payload)
            result = await adapter.execute_action({**action.payload, "type": action.type})
            app.state.automation["last_action_at"][platform] = now
            app.state.automation["daily_counts"][count_key] = count + 1
            updated = store.update_action_status(action_id, "completed")
            store.add_event(Event(conversation_id=action.conversation_id, type="action_completed",
                                  state=AgentState.COMPLETED, payload={"action_id": action_id,
                                                                        "platform": platform}))
            return ActionResult(ok=True, status="completed", action_id=action_id, platform=platform,
                                result=result, detail=updated.status if updated else None)
        except Exception as exc:
            message = str(exc).lower()
            if re.search(r"403|429|captcha|rate.?limit|uncertain|verification", message):
                app.state.automation["paused_platforms"].add(platform)
                status = "paused"
            else:
                status = "failed"
            store.update_action_status(action_id, status)
            store.add_event(Event(conversation_id=action.conversation_id, type="action_failed",
                                  state=AgentState.ERROR, payload={"action_id": action_id,
                                                                    "platform": platform,
                                                                    "status": status}))
            raise HTTPException(status_code=409, detail="Action was paused or failed safely") from exc

    @app.get("/api/automation/guardrails")
    def automation_guardrails():
        return {"require_confirmation": False,
                "autonomous_mode": bool(app.state.automation["enabled"]),
                "blocked_without_approval": [],
                "paused_platforms": sorted(app.state.automation["paused_platforms"]),
                "min_interval_seconds": app.state.automation["min_interval_seconds"],
                "daily_limit": app.state.automation["daily_limit"],
                "circuit_breakers": ["rate_limit", "captcha", "403", "429", "uncertain_result"],
                "secret_fields": ["key", "api_key", "token", "secret", "password"]}

    @app.post("/api/automation/stop")
    def stop_automation():
        app.state.automation["enabled"] = False
        return {"enabled": False}

    @app.post("/api/automation/resume")
    def resume_automation():
        app.state.automation["enabled"] = True
        app.state.automation["paused_platforms"].clear()
        return {"enabled": True, "paused_platforms": []}

    @app.post("/api/automation/platforms/{platform}/pause")
    def pause_platform(platform: str):
        app.state.automation["paused_platforms"].add(platform.lower())
        return {"platform": platform.lower(), "paused": True}

    @app.get("/api/events")
    def events(conversation_id: str | None = None, after: str | None = None,
               limit: int = Query(100, ge=1, le=500)):
        return store.list_events(conversation_id, after, limit)

    @app.get("/api/connections")
    def connections():
        return store.list_connections()

    class ConnectionRequest(BaseModel):
        name: str = Field(min_length=1, max_length=100)
        kind: str = Field(min_length=1, max_length=100)
        config: dict[str, Any] = {}
        status: str = "disconnected"

    @app.post("/api/connections", response_model=Connection)
    def create_connection(request: ConnectionRequest):
        # Do not echo secrets back: credentials can be referenced by an env key name.
        blocked_secret_terms = ("key", "token", "secret", "password")
        safe_config = {
            key: value for key, value in request.config.items()
            if key.lower().endswith("_ref")
            or not any(term in key.lower() for term in blocked_secret_terms)
        }
        if request.kind.lower() not in ADAPTERS and request.kind.lower() != "llm":
            raise HTTPException(status_code=400, detail=f"Unsupported connector kind: {request.kind}")
        status = adapter_for(request.kind, safe_config).status().message if request.kind.lower() in ADAPTERS else request.status
        return store.add_connection(Connection(name=request.name, kind=request.kind,
                                               config=safe_config, status=status))

    @app.get("/api/connections/{connection_id}/status")
    def connection_status(connection_id: str):
        connection = next((item for item in store.list_connections() if item.id == connection_id), None)
        if connection is None:
            raise HTTPException(status_code=404, detail="Connection not found")
        if connection.kind.lower() in ADAPTERS:
            adapter_status = adapter_for(connection.kind, connection.config).status()
            return {"id": connection.id, **adapter_status.__dict__}
        return {"id": connection.id, "kind": connection.kind, "configured": True,
                "available": connection.status == "connected", "message": connection.status}

    @app.put("/api/connections/{connection_id}", response_model=Connection)
    def update_connection(connection_id: str, request: ConnectionRequest):
        if request.kind.lower() not in ADAPTERS and request.kind.lower() != "llm":
            raise HTTPException(status_code=400, detail=f"Unsupported connector kind: {request.kind}")
        blocked_secret_terms = ("key", "token", "secret", "password")
        safe_config = {key: value for key, value in request.config.items()
                       if key.lower().endswith("_ref") or not any(term in key.lower() for term in blocked_secret_terms)}
        status = adapter_for(request.kind, safe_config).status().message if request.kind.lower() in ADAPTERS else request.status
        updated = store.update_connection(connection_id, name=request.name, kind=request.kind,
                                          config=safe_config, status=status)
        if updated is None:
            raise HTTPException(status_code=404, detail="Connection not found")
        return updated

    @app.post("/api/connections/{connection_id}/connect", response_model=Connection)
    def connect(connection_id: str):
        connection = next((item for item in store.list_connections() if item.id == connection_id), None)
        if connection is None:
            raise HTTPException(status_code=404, detail="Connection not found")
        if connection.kind.lower() in ADAPTERS:
            adapter_status = adapter_for(connection.kind, connection.config).status()
            if not adapter_status.configured:
                raise HTTPException(status_code=409, detail="Connection requires configuration")
            if not adapter_status.available:
                raise HTTPException(status_code=409, detail=adapter_status.message)
        updated = store.update_connection_status(connection_id, "connected")
        return updated

    @app.get("/api/events/stream")
    async def event_stream(conversation_id: str | None = None, after: str | None = None):
        """Small polling-to-SSE bridge for PWA clients behind simple proxies."""
        async def generate():
            cursor = after
            for _ in range(30):
                items = store.list_events(conversation_id, cursor, 100)
                for item in items:
                    cursor = item.created_at
                    yield f"data: {json.dumps(item.model_dump(), ensure_ascii=False)}\n\n"
                await asyncio.sleep(1)
            yield ": keep-alive\n\n"
        return StreamingResponse(generate(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    # A production build is optional. Mounting it here lets `uvicorn app.main`
    # serve the same PWA on a Windows LAN without a second web server.
    dist = Path(__file__).resolve().parents[2] / "frontend" / "dist"
    if dist.exists() and (dist / "index.html").exists():
        app.mount("/", StaticFiles(directory=str(dist), html=True), name="frontend")

    return app


app = create_app()

