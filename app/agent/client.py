from __future__ import annotations

from typing import Any, Protocol

from pydantic import BaseModel, Field

from app.config import AgentSettings


class ModelTurn(BaseModel):
    response_id: str | None = None
    output_items: list[dict[str, Any]] = Field(default_factory=list)
    output_text: str = ""
    usage: dict[str, Any] = Field(default_factory=dict)


class ModelClient(Protocol):
    def create_response(
        self,
        *,
        input_items: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        instructions: str,
        timeout_seconds: float,
    ) -> ModelTurn: ...


class DeepSeekResponsesClient:
    def __init__(self, settings: AgentSettings) -> None:
        from openai import OpenAI

        self.settings = settings
        self._client = OpenAI(
            api_key=settings.api_key.get_secret_value(),
            base_url=settings.base_url,
            timeout=settings.request_timeout_seconds,
            max_retries=1,
        )

    def create_response(
        self,
        *,
        input_items: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        instructions: str,
        timeout_seconds: float,
    ) -> ModelTurn:
        response = self._client.with_options(timeout=timeout_seconds).responses.create(
            model=self.settings.model,
            instructions=instructions,
            input=input_items,
            tools=tools,
            tool_choice="auto",
            reasoning={"effort": self.settings.reasoning_effort},
            max_output_tokens=self.settings.max_output_tokens,
        )
        output_items = [item.model_dump(mode="json", exclude_none=True) for item in response.output]
        usage = (
            response.usage.model_dump(mode="json", exclude_none=True)
            if response.usage is not None
            else {}
        )
        return ModelTurn(
            response_id=response.id,
            output_items=output_items,
            output_text=response.output_text or "",
            usage=usage,
        )
