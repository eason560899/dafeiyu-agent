from dataclasses import dataclass
from typing import Any, AsyncIterator

import httpx

from .config import Settings


@dataclass
class ProviderResult:
    content: str
    provider: str
    model: str


class ProviderError(RuntimeError):
    """Safe provider error that can be represented without exposing credentials."""

    def __init__(self, message: str, *, status_code: int | None = None, retryable: bool = False):
        super().__init__(message)
        self.status_code = status_code
        self.retryable = retryable


class OpenAICompatibleProvider:
    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None):
        self.settings = settings
        self._client = client

    async def complete(self, messages: list[dict[str, str]], context: dict[str, Any] | None = None) -> ProviderResult:
        if self.settings.mock_mode or not self.settings.provider_api_key:
            user = next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")
            return ProviderResult(
                content=f"Mock response: {user}", provider="mock", model="mock",
            )
        headers = {"Authorization": f"Bearer {self.settings.provider_api_key}", "Content-Type": "application/json"}
        body: dict[str, Any] = {"model": self.settings.provider_model, "messages": messages}
        if context:
            body["metadata"] = {"context": context}
        close_client = self._client is None
        client = self._client or httpx.AsyncClient(timeout=self.settings.provider_timeout_seconds)
        try:
            response = await client.post(
                f"{self.settings.provider_base_url.rstrip('/')}/chat/completions",
                headers=headers, json=body,
            )
            response.raise_for_status()
            payload = response.json()
            content = payload["choices"][0]["message"]["content"]
            if not isinstance(content, str):
                raise ValueError("Provider returned non-text content")
            return ProviderResult(content=content, provider="openai-compatible", model=payload.get("model", self.settings.provider_model))
        except httpx.HTTPStatusError as exc:
            raise ProviderError("Provider returned an error", status_code=exc.response.status_code,
                                retryable=exc.response.status_code in {408, 409, 429} or exc.response.status_code >= 500) from exc
        except (httpx.HTTPError, KeyError, IndexError, ValueError) as exc:
            raise ProviderError("Provider response was invalid", retryable=True) from exc
        finally:
            if close_client:
                await client.aclose()

    async def stream(self, messages: list[dict[str, str]], context: dict[str, Any] | None = None) -> AsyncIterator[str]:
        """Yield text deltas from an OpenAI-compatible SSE response.

        Mock mode yields deterministic small chunks. Remote errors are converted to
        :class:`ProviderError`; raw response bodies and authorization headers never
        leave this module.
        """
        if self.settings.mock_mode or not self.settings.provider_api_key:
            user = next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")
            text = f"Mock response: {user}"
            for index in range(0, len(text), 16):
                yield text[index : index + 16]
            return

        headers = {"Authorization": f"Bearer {self.settings.provider_api_key}", "Content-Type": "application/json"}
        body: dict[str, Any] = {"model": self.settings.provider_model, "messages": messages, "stream": True}
        if context:
            body["metadata"] = {"context": context}
        close_client = self._client is None
        client = self._client or httpx.AsyncClient(timeout=self.settings.provider_timeout_seconds)
        try:
            async with client.stream("POST", f"{self.settings.provider_base_url.rstrip('/')}/chat/completions",
                                     headers=headers, json=body) as response:
                if response.status_code >= 400:
                    raise ProviderError("Provider returned an error", status_code=response.status_code,
                                        retryable=response.status_code in {408, 409, 429} or response.status_code >= 500)
                async for line in response.aiter_lines():
                    line = line.strip()
                    if not line or line.startswith(":"):
                        continue
                    if line.startswith("data:"):
                        line = line[5:].strip()
                    if line == "[DONE]":
                        break
                    try:
                        payload = __import__("json").loads(line)
                        delta = payload.get("choices", [{}])[0].get("delta", {}).get("content")
                        if isinstance(delta, str) and delta:
                            yield delta
                    except (ValueError, IndexError, TypeError):
                        # Ignore keepalive/non-JSON lines in otherwise valid SSE.
                        continue
        except ProviderError:
            raise
        except httpx.HTTPError as exc:
            raise ProviderError("Provider stream failed", retryable=True) from exc
        finally:
            if close_client:
                await client.aclose()

