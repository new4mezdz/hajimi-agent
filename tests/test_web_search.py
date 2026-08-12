import httpx
import pytest

from agent_product.services.web_search import DeepSeekWebSearchClient


@pytest.mark.asyncio
async def test_deepseek_web_search_forces_tool_and_returns_sources() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["x-api-key"] == "test-key"
        payload = request.read()
        assert b'"tool_choice":{"type":"tool","name":"web_search"}' in payload
        assert b'"max_uses":2' in payload
        return httpx.Response(
            200,
            json={
                "content": [
                    {
                        "type": "web_search_tool_result",
                        "content": [
                            {
                                "type": "web_search_result",
                                "title": "Official source",
                                "url": "https://example.com/official",
                                "page_age": "2026-07-24",
                            },
                            {
                                "type": "web_search_result",
                                "title": "Duplicate",
                                "url": "https://example.com/official",
                            },
                        ],
                    },
                    {"type": "text", "text": "Verified answer."},
                ]
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = DeepSeekWebSearchClient(
            api_key="test-key",
            base_url="https://api.deepseek.com/anthropic",
            model_name="deepseek-v4-flash",
            max_uses=2,
            http_client=http_client,
        )

        result = await client.search("latest information")

    assert result == {
        "query": "latest information",
        "answer": "Verified answer.",
        "sources": [
            {
                "url": "https://example.com/official",
                "title": "Official source",
                "page_age": "2026-07-24",
            }
        ],
    }
