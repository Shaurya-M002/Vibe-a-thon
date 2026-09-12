"""Official Google Gen AI SDK adapter; Governor executes every tool itself."""

from typing import Protocol

from google import genai
from google.genai import types

from governor.config import Settings


class Model(Protocol):
    async def generate(
        self, history: list[types.Content], tools: list[types.FunctionDeclaration], instruction: str
    ) -> types.GenerateContentResponse: ...


class GeminiModel:
    def __init__(self, settings: Settings, *, client: genai.Client | None = None):
        self.settings = settings
        if client is not None:
            self.client = client
            return
        settings.require_credentials()
        options = types.HttpOptions(
            timeout=settings.model_timeout_seconds * 1000,
            retry_options=types.HttpRetryOptions(attempts=1),
        )
        if settings.backend == "vertex":
            self.client = genai.Client(
                enterprise=True,
                project=settings.project,
                location=settings.location,
                http_options=options,
            )
        else:
            self.client = genai.Client(
                enterprise=False,
                api_key=settings.api_key.get_secret_value(),
                http_options=options,
            )

    async def generate(
        self, history: list[types.Content], tools: list[types.FunctionDeclaration], instruction: str
    ) -> types.GenerateContentResponse:
        return await self.client.aio.models.generate_content(
            model=self.settings.model,
            contents=history,
            config=types.GenerateContentConfig(
                system_instruction=instruction,
                tools=[types.Tool(function_declarations=tools)],
                automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
                max_output_tokens=2048,
            ),
        )

    async def close(self) -> None:
        await self.client.aio.aclose()
        self.client.close()
