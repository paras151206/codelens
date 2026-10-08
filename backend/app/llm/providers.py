import asyncio
import json
import logging
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
import httpx
from app.core.config import settings

logger = logging.getLogger(__name__)


class LLMProvider(ABC):
    name: str
    models: list[str]
    selected: str

    @abstractmethod
    async def discover(self) -> None: ...

    @abstractmethod
    def stream(self, messages: list[dict]) -> AsyncIterator[str]: ...


class Provider(LLMProvider):
    def __init__(self, name: str, key: str, preferred: str, base: str) -> None:
        self.name, self.key, self.base = name, key, base.rstrip("/")
        self.preferred = [m.strip() for m in preferred.split(",") if m.strip()]
        self.models: list[str] = []
        self.selected = ""

    def headers(self) -> dict:
        return (
            {"x-goog-api-key": self.key} if self.name == "gemini" else {"Authorization": f"Bearer {self.key}"}
        )

    async def discover(self) -> None:
        async with httpx.AsyncClient(timeout=20) as client:
            endpoint = "/api/tags" if self.name == "ollama" else "/models"
            response = await client.get(self.base + endpoint, headers=self.headers())
            response.raise_for_status()
            body = response.json()
            rows = body.get("models", body.get("data", []))
            if self.name == "gemini":
                rows = [r for r in rows if "generateContent" in r.get("supportedGenerationMethods", [])]
            rows = sorted(
                rows,
                key=lambda r: (
                    r.get("context_window", r.get("inputTokenLimit", 0)),
                    r.get("id", r.get("name", "")),
                ),
                reverse=True,
            )
            self.models = [r.get("id", r.get("name", "")).removeprefix("models/") for r in rows]
            self.models = [
                m
                for m in self.models
                if not any(
                    word in m.lower()
                    for word in ("whisper", "tts", "guard", "embedding", "orpheus", "image", "audio", "live")
                )
            ]
            self.selected = next((m for m in self.preferred if m in self.models), next(iter(self.models), ""))
            logger.info("%s available models: %s; selected: %s", self.name, self.models, self.selected)
            if not self.selected:
                raise RuntimeError("No text generation models available")

    async def stream(self, messages: list[dict]) -> AsyncIterator[str]:
        if self.name == "gemini":
            endpoint = f"/models/{self.selected}:streamGenerateContent?alt=sse"
            body = {
                "systemInstruction": {"parts": [{"text": messages[0]["content"]}]},
                "contents": [
                    {
                        "role": "model" if m["role"] == "assistant" else "user",
                        "parts": [{"text": m["content"]}],
                    }
                    for m in messages[1:]
                ],
                "generationConfig": {"temperature": 0.15, "maxOutputTokens": 2200},
            }
        else:
            endpoint = "/api/chat" if self.name == "ollama" else "/chat/completions"
            body = {"model": self.selected, "messages": messages, "stream": True}
            if self.name != "ollama":
                body.update(temperature=0.15, max_tokens=2200)
            else:
                body["options"] = {"temperature": 0.15, "num_predict": 2200}
        async with httpx.AsyncClient(timeout=httpx.Timeout(90, connect=15)) as client:
            async with client.stream(
                "POST", self.base + endpoint, headers=self.headers(), json=body
            ) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line or line.startswith(":"):
                        continue
                    if self.name != "ollama" and not line.startswith("data: "):
                        continue
                    payload = line.removeprefix("data: ")
                    if payload == "[DONE]":
                        break
                    data = json.loads(payload)
                    if self.name == "gemini":
                        piece = "".join(
                            p.get("text", "")
                            for c in data.get("candidates", [])
                            for p in c.get("content", {}).get("parts", [])
                            if not p.get("thought")
                        )
                    elif self.name == "ollama":
                        piece = data.get("message", {}).get("content", "")
                    else:
                        piece = "".join(
                            c.get("delta", {}).get("content", "") or "" for c in data.get("choices", [])
                        )
                    if piece:
                        yield piece


class ProviderPool:
    def __init__(self, providers: list[LLMProvider] | None = None) -> None:
        self.providers = (
            providers
            if providers is not None
            else [
                Provider(name, key, models, base)
                for name, key, models, base in [
                    ("groq", settings.groq_api_key, settings.groq_models, "https://api.groq.com/openai/v1"),
                    (
                        "gemini",
                        settings.gemini_api_key,
                        settings.gemini_models,
                        "https://generativelanguage.googleapis.com/v1beta",
                    ),
                    (
                        "ollama",
                        "local" if settings.ollama_url else "",
                        settings.ollama_models,
                        settings.ollama_url,
                    ),
                ]
                if key
            ]
        )
        self.status: dict = {}

    async def discover(self) -> None:
        for provider in self.providers:
            try:
                await provider.discover()
                self.status[provider.name] = {"model": provider.selected, "models": provider.models}
            except Exception as exc:
                self.status[provider.name] = {"error": type(exc).__name__}

    async def stream(self, messages: list[dict]) -> AsyncIterator[dict]:
        for provider in self.providers:
            failed_model = ""
            for attempt in range(2):
                try:
                    if not provider.selected or attempt:
                        await provider.discover()
                    if attempt:
                        provider.selected = next(
                            (m for m in provider.models if m != failed_model), provider.selected
                        )
                    yield {"type": "provider", "provider": provider.name, "model": provider.selected}
                    received = False
                    async for token in provider.stream(messages):
                        received = True
                        yield {"type": "token", "text": token}
                    if not received:
                        raise RuntimeError("Provider returned an empty answer")
                    return
                except (httpx.HTTPError, RuntimeError, ValueError, KeyError) as exc:
                    failed_model = provider.selected
                    yield {
                        "type": "reset",
                        "reason": f"{provider.name} unavailable; retrying or switching provider",
                    }
                    logger.warning("Provider %s failed: %s", provider.name, type(exc).__name__)
                    delay = 0.5 * (attempt + 1)
                    if isinstance(exc, httpx.HTTPStatusError):
                        try:
                            delay = min(
                                3.0, max(delay, float(exc.response.headers.get("retry-after", delay)))
                            )
                        except ValueError:
                            pass
                    await asyncio.sleep(delay)
        raise RuntimeError("No LLM provider is available. Add a free-tier key or configure local Ollama.")

    async def complete(self, messages: list[dict]) -> str:
        answer = ""
        async for event in self.stream(messages):
            if event["type"] == "reset":
                answer = ""
            elif event["type"] == "token":
                answer += event["text"]
        return answer
