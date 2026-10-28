import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parents[1]))

from app.config import Settings
from app.main import create_app


@pytest.fixture()
def client():
    app = create_app(Settings(database_url="sqlite:///:memory:", mock_mode=True))
    with TestClient(app) as test_client:
        yield test_client


def test_health(client):
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_chat_persists_candidate_and_events(client):
    response = client.post("/api/chat", json={"message": "hello"})
    assert response.status_code == 200
    payload = response.json()
    assert payload["message"] == "Mock response: hello"
    conversation_id = payload["conversation_id"]
    assert client.get("/api/candidates", params={"conversation_id": conversation_id}).json()
    assert len(client.get("/api/events", params={"conversation_id": conversation_id}).json()) == 2


def test_action_and_connection_do_not_echo_secrets(client):
    assert client.post("/api/actions", json={"conversation_id": "c1", "type": "approve"}).status_code == 200
    response = client.post("/api/connections", json={"name": "OpenAI", "kind": "llm",
                                                       "config": {"api_key": "secret", "region": "us"}})
    assert response.status_code == 200
    assert "api_key" not in response.json()["config"]


def test_stream_contains_deltas_and_persists_result(client):
    response = client.post("/api/chat/stream", json={"message": "stream me"})
    assert response.status_code == 200
    lines = [line for line in response.text.splitlines() if line]
    assert any('"type": "delta"' in line for line in lines)
    assert any('"type": "message"' in line for line in lines)


def test_onebot_mock_action_is_idempotent_and_executable(client):
    first = client.post("/api/actions", json={
        "conversation_id": "c1", "type": "send", "platform": "onebot",
        "idempotency_key": "same-key", "payload": {"target": "group:1", "content": "hello",
                                                          "connection_config": {"base_url": "http://onebot", "mock": True}},
    }).json()
    second = client.post("/api/actions", json={
        "conversation_id": "c1", "type": "send", "platform": "onebot",
        "idempotency_key": "same-key", "payload": {"target": "group:1", "content": "changed",
                                                          "connection_config": {"base_url": "http://onebot", "mock": True}},
    }).json()
    assert first["id"] == second["id"]
    result = client.post(f"/api/actions/{first['id']}/execute")
    assert result.status_code == 200
    assert result.json()["status"] == "completed"


def test_device_token_and_global_stop():
    app = create_app(Settings(database_url="sqlite:///:memory:", mock_mode=True, device_token="local-token"))
    with TestClient(app) as secured:
        assert secured.get("/api/providers").status_code == 401
        assert secured.get("/api/providers", headers={"X-Device-Token": "local-token"}).status_code == 200
        assert secured.post("/api/automation/stop", headers={"X-Device-Token": "local-token"}).json()["enabled"] is False


def test_task_discovery_config_is_validated_and_persisted(client):
    response = client.put("/api/tasks/config", json={"enabled": True, "schedule": "*/15 * * * *",
                                                       "sources": ["bilibili", "douyin"], "prompt": "find clips"})
    assert response.status_code == 200
    assert client.get("/api/tasks/config").json()["sources"] == ["bilibili", "douyin"]


def test_connection_update_keeps_secret_references_only(client):
    created = client.post("/api/connections", json={"name": "QQ", "kind": "qq",
                                                     "config": {"base_url": "http://onebot"}}).json()
    updated = client.put(f"/api/connections/{created['id']}", json={
        "name": "QQ main", "kind": "qq",
        "config": {"base_url": "http://onebot", "access_token_ref": "QQ_TOKEN", "access_token": "raw-secret"},
    })
    assert updated.status_code == 200
    assert updated.json()["config"] == {"base_url": "http://onebot", "access_token_ref": "QQ_TOKEN"}

