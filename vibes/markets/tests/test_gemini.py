import json

import httpx
from google import genai
from google.genai import types

from governor.config import Settings
from governor.gemini import GeminiModel


async def test_official_sdk_serializes_manual_tool_history():
    requests = []

    def handler(request):
        requests.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "candidates": [
                    {
                        "content": {"role": "model", "parts": [{"text": "Done."}]},
                        "finishReason": "STOP",
                    }
                ]
            },
        )

    client = genai.Client(
        enterprise=False,
        api_key="test-key",
        http_options=types.HttpOptions(
            async_client_args={"transport": httpx.MockTransport(handler)},
        ),
    )
    model = GeminiModel(Settings(), client=client)
    history = [
        types.Content(role="user", parts=[types.Part.from_text(text="Read my budget.")]),
        types.Content(
            role="model",
            parts=[
                types.Part(
                    function_call=types.FunctionCall(name="get_budget", args={}, id="call-1"),
                    thought_signature=b"opaque-signature",
                )
            ],
        ),
        types.Content(
            role="user",
            parts=[
                types.Part(
                    function_response=types.FunctionResponse(
                        name="get_budget",
                        id="call-1",
                        response={"available": "10000"},
                    )
                )
            ],
        ),
    ]
    try:
        result = await model.generate(
            history,
            [
                types.FunctionDeclaration(
                    name="get_budget",
                    parameters_json_schema={"type": "object", "properties": {}},
                )
            ],
            "Use the spending gate.",
        )
    finally:
        await model.close()
    assert result.candidates[0].content.parts[0].text == "Done."
    assert len(requests) == 1
    body = requests[0]
    assert body["contents"][1]["parts"][0]["thoughtSignature"] == "b3BhcXVlLXNpZ25hdHVyZQ=="
    assert body["contents"][2]["parts"][0]["functionResponse"]["id"] == "call-1"
    assert body["tools"][0]["functionDeclarations"][0]["name"] == "get_budget"
