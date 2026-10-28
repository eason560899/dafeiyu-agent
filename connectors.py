"""Small, dependency-light platform adapters.

The project never bundles account credentials or browser profiles. Adapters use
explicit local executables/bridges supplied by the user and return a stable
JSON-shaped contract so the rest of the agent remains testable without real
platform accounts.
"""

from abc import ABC
import asyncio
import json
import os
import shutil
from dataclasses import dataclass
from typing import Any

import httpx


@dataclass
class AdapterStatus:
    kind: str
    configured: bool
    available: bool
    message: str


class SourceAdapter(ABC):
    kind: str
    required_keys: tuple[str, ...] = ()
    capabilities: tuple[str, ...] = ("discover", "read")

    def __init__(self, config: dict[str, Any] | None = None):
        self.config = config or {}

    def status(self) -> AdapterStatus:
        configured = all(bool(self.config.get(key)) for key in self.required_keys)
        if not configured:
            return AdapterStatus(self.kind, False, False, "Missing required configuration")
        return AdapterStatus(self.kind, True, False, "Configured; runtime bridge is unavailable")

    async def discover(self, query: str) -> list[dict[str, Any]]:
        raise RuntimeError(f"{self.kind} discovery connector is not installed")

    async def read(self, content_id: str) -> dict[str, Any]:
        raise RuntimeError(f"{self.kind} read connector is not installed")

    async def draft_action(self, action: dict[str, Any]) -> dict[str, Any]:
        return {"ok": True, "status": "draft", "action": action, "adapter": self.kind}

    async def execute_action(self, action: dict[str, Any]) -> dict[str, Any]:
        raise RuntimeError(f"{self.kind} write connector is not installed")

    async def fetch(self, query: str) -> list[dict[str, Any]]:
        return await self.discover(query)

    async def send(self, target: str, content: str) -> dict[str, Any]:
        return await self.execute_action({"type": "send", "target": target, "content": content})


class BiliAdapter(SourceAdapter):
    kind = "bilibili"
    required_keys = ("credential_ref",)
    capabilities = ("discover", "read", "comment", "mention", "dm")

    def _executable(self) -> str:
        configured = str(self.config.get("executable", "bili-cli"))
        return configured if os.path.isabs(configured) else (shutil.which(configured) or configured)

    async def _run_json(self, *args: str) -> Any:
        try:
            process = await asyncio.create_subprocess_exec(
                self._executable(), *args, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(process.communicate(), timeout=20)
        except (FileNotFoundError, asyncio.TimeoutError) as exc:
            raise RuntimeError("Install bili-cli and configure its account before using B站") from exc
        if process.returncode != 0:
            raise RuntimeError("bili-cli returned an error")
        try:
            return json.loads(stdout.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
            raise RuntimeError("bili-cli returned invalid JSON") from exc

    def status(self) -> AdapterStatus:
        configured = all(bool(self.config.get(key)) for key in self.required_keys)
        available = configured and bool(shutil.which(str(self.config.get("executable", "bili-cli"))))
        return AdapterStatus(self.kind, configured, available,
                             "Ready to call bili-cli" if available else
                             "Install bili-cli and configure its account")

    async def discover(self, query: str) -> list[dict[str, Any]]:
        # bili-cli's current command shape is `bili search video <keyword>`;
        # its JSON envelope is stable even when the CLI prints diagnostics.
        payload = await self._run_json("search", "video", query)
        if isinstance(payload, dict) and payload.get("ok") is False:
            raise RuntimeError("bili-cli search failed")
        payload = payload.get("data", payload) if isinstance(payload, dict) else payload
        if isinstance(payload, list):
            return payload
        if isinstance(payload, dict):
            return payload.get("items") or payload.get("data") or []
        return []

    async def read(self, content_id: str) -> dict[str, Any]:
        payload = await self._run_json("video", "watch", content_id)
        if isinstance(payload, dict) and payload.get("ok") is False:
            raise RuntimeError("bili-cli video read failed")
        payload = payload.get("data", payload) if isinstance(payload, dict) else payload
        return payload if isinstance(payload, dict) else {"content": payload}


class DouyinAdapter(SourceAdapter):
    kind = "douyin"
    required_keys = ("browser_profile",)
    capabilities = ("discover", "read", "comment", "dm")

    def status(self) -> AdapterStatus:
        configured = all(bool(self.config.get(key)) for key in self.required_keys)
        available = configured and (bool(self.config.get("mock_items")) or bool(shutil.which("playwright")))
        message = "Using configured mock feed" if self.config.get("mock_items") else "Browser bridge required"
        return AdapterStatus(self.kind, configured, available, message)

    async def discover(self, query: str) -> list[dict[str, Any]]:
        if isinstance(self.config.get("mock_items"), list):
            return [item for item in self.config["mock_items"]
                    if not query or query.lower() in json.dumps(item, ensure_ascii=False).lower()]
        raise RuntimeError("Configure a Playwright browser bridge for 抖音 discovery")


class OneBotAdapter(SourceAdapter):
    kind = "onebot"
    required_keys = ("base_url",)
    capabilities = ("receive", "send", "mention", "group_chat")

    def status(self) -> AdapterStatus:
        configured = all(bool(self.config.get(key)) for key in self.required_keys)
        return AdapterStatus(self.kind, configured, configured and bool(self.config.get("mock", False)),
                             "Mock OneBot enabled" if self.config.get("mock") else
                             "OneBot HTTP endpoint configured; probe on first send")

    async def execute_action(self, action: dict[str, Any]) -> dict[str, Any]:
        target = str(action.get("target") or action.get("user_id") or action.get("group_id") or "")
        content = str(action.get("content") or action.get("message") or "")
        if not target or not content:
            raise RuntimeError("OneBot action requires target and content")
        if self.config.get("mock", False):
            return {"ok": True, "mock": True, "adapter": self.kind, "target": target, "content": content}
        payload: dict[str, Any] = {"message": content}
        if target.startswith("group:"):
            payload.update({"message_type": "group", "group_id": target.split(":", 1)[1]})
        else:
            payload.update({"message_type": "private", "user_id": target.split(":", 1)[-1]})
        headers = {}
        # Credentials are referenced by environment variable name so they never
        # pass through the connections API or get persisted in SQLite.
        token_ref = self.config.get("access_token_ref")
        token = os.environ.get(str(token_ref), "") if token_ref else ""
        if token:
            headers["Authorization"] = f"Bearer {token}"
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.post(f"{str(self.config['base_url']).rstrip('/')}/send_msg",
                                         json=payload, headers=headers)
            if response.status_code >= 400:
                raise RuntimeError(f"OneBot returned HTTP {response.status_code}")
            data = response.json()
        if isinstance(data, dict) and data.get("status") == "failed":
            raise RuntimeError("OneBot rejected the message")
        return {"ok": True, "adapter": self.kind, "response": data}


class QQAdapter(OneBotAdapter):
    kind = "qq"


class WeChatAdapter(SourceAdapter):
    kind = "wechat"
    required_keys = ("bridge_url",)
    capabilities = ("receive", "send", "mention", "group_chat")

    def status(self) -> AdapterStatus:
        configured = all(bool(self.config.get(key)) for key in self.required_keys)
        available = configured and bool(self.config.get("mock", False))
        return AdapterStatus(self.kind, configured, available,
                             "Mock bridge enabled" if self.config.get("mock") else
                             ("External WeChat bridge configured; probe on first send"
                              if configured else "External WeChat bridge required (dsh-im/Tencent iLink is not bundled)"))

    async def execute_action(self, action: dict[str, Any]) -> dict[str, Any]:
        if self.config.get("mock", False):
            return {"ok": True, "mock": True, "adapter": self.kind,
                    "target": action.get("target"), "content": action.get("content", "")}
        content = str(action.get("content") or action.get("message") or "")
        if not content:
            raise RuntimeError("WeChat action requires content")
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.post(str(self.config["bridge_url"]).rstrip("/"),
                                         json={"action": action.get("type", "send"),
                                               "target": action.get("target"), "content": content})
            if response.status_code >= 400:
                raise RuntimeError(f"WeChat bridge returned HTTP {response.status_code}")
            data = response.json()
        return {"ok": True, "adapter": self.kind, "response": data}


ADAPTERS: dict[str, type[SourceAdapter]] = {
    "bilibili": BiliAdapter,
    "douyin": DouyinAdapter,
    "onebot": OneBotAdapter,
    "qq": QQAdapter,
    "wechat": WeChatAdapter,
}


def adapter_for(kind: str, config: dict[str, Any] | None = None) -> SourceAdapter:
    adapter_type = ADAPTERS.get(kind.lower())
    if adapter_type is None:
        raise ValueError(f"Unsupported connector kind: {kind}")
    return adapter_type(config)
