import httpx
import pytest
from app.llm.providers import LLMProvider, ProviderPool, Provider


class FakeProvider(LLMProvider):
    def __init__(self, name: str, fails: bool) -> None:
        self.name, self.fails = name, fails
        self.models, self.selected = ["configured-model"], "configured-model"

    async def discover(self) -> None:
        self.selected = self.models[0]

    async def stream(self, messages):
        if self.fails:
            yield "partial wrong answer"
            raise httpx.HTTPStatusError(
                "rate limited",
                request=httpx.Request("POST", "https://example.com"),
                response=httpx.Response(429),
            )
        yield "grounded answer"


@pytest.mark.asyncio
async def test_fallback_resets_partial_output(monkeypatch) -> None:
    async def no_wait(seconds):
        return None

    monkeypatch.setattr("app.llm.providers.asyncio.sleep", no_wait)
    pool = ProviderPool([FakeProvider("primary", True), FakeProvider("fallback", False)])
    assert await pool.complete([{"role": "user", "content": "question"}]) == "grounded answer"


@pytest.mark.asyncio
async def test_no_provider_is_explicit_error() -> None:
    with pytest.raises(RuntimeError, match="No LLM"):
        await ProviderPool([]).complete([])


@pytest.mark.asyncio
async def test_model_discovery_prefers_config_and_filters_audio(monkeypatch) -> None:
    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def get(self, *args, **kwargs):
            return httpx.Response(
                200,
                json={
                    "data": [
                        {"id": "whisper-audio"},
                        {"id": "available", "context_window": 1000},
                        {"id": "preferred", "context_window": 500},
                    ]
                },
                request=httpx.Request("GET", "https://example.com"),
            )

    monkeypatch.setattr("app.llm.providers.httpx.AsyncClient", lambda **kwargs: Client())
    provider = Provider("groq", "not-a-real-key", "removed,preferred", "https://example.com")
    await provider.discover()
    assert provider.selected == "preferred"
    provider.preferred = ["deprecated"]
    await provider.discover()
    assert provider.selected == "available"
    assert "whisper-audio" not in provider.models
