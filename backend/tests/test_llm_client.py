from __future__ import annotations

import asyncio

import httpx

from app.schemas.llm import LlmNetworkConfig
from app.services.llm_client import LlmClient
from app.services.llm_config_service import RuntimeLlmConfig


def make_runtime_config(*, extra_body: dict | None = None) -> RuntimeLlmConfig:
    return RuntimeLlmConfig(
        scope="user",
        provider_kind="litellm",
        api_format="openai-compatible",
        base_url="http://example.com/v1",
        model="demo-model",
        api_key="demo-key",
        extra_headers={},
        extra_body=extra_body or {},
        network=LlmNetworkConfig(enabled=True),
        enabled=True,
    )


class FakeResponse:
    def __init__(self, *, status_code: int = 200, json_data: dict | None = None, lines: list[str] | None = None):
        self.status_code = status_code
        self._json_data = json_data or {}
        self._lines = lines or []
        self.request = httpx.Request("POST", "http://example.com/v1/chat/completions")

    @property
    def is_error(self) -> bool:
        return self.status_code >= 400

    async def aread(self) -> bytes:
        return b""

    def raise_for_status(self) -> None:
        if self.is_error:
            raise httpx.HTTPStatusError(
                f"status {self.status_code}",
                request=self.request,
                response=self,
            )

    def json(self) -> dict:
        return self._json_data

    async def aiter_lines(self):
        for line in self._lines:
            yield line


class FakeStreamContext:
    def __init__(self, response: FakeResponse):
        self._response = response

    async def __aenter__(self) -> FakeResponse:
        return self._response

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        return False


class FakeAsyncClient:
    post_responses: list[FakeResponse] = []
    stream_responses: list[FakeResponse] = []
    post_payloads: list[dict] = []
    stream_payloads: list[dict] = []

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self) -> FakeAsyncClient:
        return self

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        return False

    async def post(self, url: str, *, headers: dict, json: dict) -> FakeResponse:
        self.__class__.post_payloads.append(json)
        response = self.__class__.post_responses.pop(0)
        response.request = httpx.Request("POST", url)
        return response

    def stream(self, method: str, url: str, *, headers: dict, json: dict) -> FakeStreamContext:
        self.__class__.stream_payloads.append(json)
        response = self.__class__.stream_responses.pop(0)
        response.request = httpx.Request(method, url)
        return FakeStreamContext(response)

    @classmethod
    def reset(cls) -> None:
        cls.post_responses = []
        cls.stream_responses = []
        cls.post_payloads = []
        cls.stream_payloads = []


def test_payload_strips_tool_fields_when_tools_are_disabled_via_extra_body():
    client = LlmClient()
    config = make_runtime_config(extra_body={"tools": []})

    payload = client._payload(
        config,
        [{"role": "user", "content": "hi"}],
        [{"type": "function", "function": {"name": "demo", "parameters": {}}}],
        stream=True,
    )

    assert "tools" not in payload
    assert "tool_choice" not in payload
    assert "tool_stream" not in payload


def test_stream_chat_completion_retries_without_tools_on_gateway_timeout(monkeypatch):
    FakeAsyncClient.reset()
    FakeAsyncClient.stream_responses = [
        FakeResponse(status_code=504),
        FakeResponse(
            lines=[
                'data: {"choices":[{"delta":{"content":"hello"}}]}',
                "data: [DONE]",
            ]
        ),
    ]
    monkeypatch.setattr("app.services.llm_client.httpx.AsyncClient", FakeAsyncClient)

    client = LlmClient()
    config = make_runtime_config()

    async def collect() -> list[dict]:
        items: list[dict] = []
        async for chunk in client.stream_chat_completion(
            config,
            [{"role": "user", "content": "hi"}],
            [{"type": "function", "function": {"name": "demo", "parameters": {}}}],
        ):
            items.append(chunk)
        return items

    chunks = asyncio.run(collect())

    assert chunks == [{"choices": [{"delta": {"content": "hello"}}]}]
    assert len(FakeAsyncClient.stream_payloads) == 2
    assert "tools" in FakeAsyncClient.stream_payloads[0]
    assert "tools" not in FakeAsyncClient.stream_payloads[1]
    assert "tool_choice" not in FakeAsyncClient.stream_payloads[1]


def test_create_chat_completion_retries_without_tools_on_gateway_timeout(monkeypatch):
    FakeAsyncClient.reset()
    FakeAsyncClient.post_responses = [
        FakeResponse(status_code=504),
        FakeResponse(json_data={"choices": [{"message": {"role": "assistant", "content": "ok"}}]}),
    ]
    monkeypatch.setattr("app.services.llm_client.httpx.AsyncClient", FakeAsyncClient)

    client = LlmClient()
    config = make_runtime_config()

    message = asyncio.run(
        client.create_chat_completion(
            config,
            [{"role": "user", "content": "hi"}],
            [{"type": "function", "function": {"name": "demo", "parameters": {}}}],
        )
    )

    assert message == {"role": "assistant", "content": "ok"}
    assert len(FakeAsyncClient.post_payloads) == 2
    assert "tools" in FakeAsyncClient.post_payloads[0]
    assert "tools" not in FakeAsyncClient.post_payloads[1]
