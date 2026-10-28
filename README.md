# Agent backend

FastAPI service for the local agent workspace. The service uses SQLite for durable
state and can call any OpenAI-compatible `/chat/completions` endpoint. It starts
in deterministic mock mode until a local provider key is configured.

## Run

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e .
uvicorn app.main:app --app-dir backend --reload
```

The API is available under `/api`; `/health` is also provided for probes.

For local MCP clients, run `python -m app.mcp_server` from the repository root
and configure the command with `PYTHONPATH=backend`. The stdio server exposes
health, chat, candidates and audited actions; external side effects still use
the HTTP approval/execute routes.

## Configuration

| Variable | Default | Description |
| --- | --- | --- |
| `AI_DATABASE_URL` | `sqlite:///./agent.db` | SQLite URL (`sqlite:///...`) |
| `DAFEIYU_BASE_URL` or `DAFEIYU_PROVIDER_BASE_URL` | `https://api.deepseek.com` | OpenAI-compatible API root |
| `DAFEIYU_API_KEY` or `AI_PROVIDER_API_KEY` | empty | API key; never returned by API responses |
| `DAFEIYU_MODEL` or `DAFEIYU_PROVIDER_MODEL` | `deepseek-flash` | Chat model (DeepSeek V4.1 Flash alias) |
| `AI_MOCK_MODE` | `true` | Deterministic local provider |
| `AI_CORS_ORIGINS` | `http://localhost:3000,http://localhost:5173` | Allowed origins |
| `DAFEIYU_DEVICE_TOKEN` | empty | Optional LAN/Tailscale device token |
| `DAFEIYU_AUTOMATION_ENABLED` | `true` | Process-local automation kill switch |


