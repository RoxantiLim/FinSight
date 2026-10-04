from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class ToolTrace(BaseModel):
    sequence_no: int
    tool_name: str
    status: Literal["succeeded", "failed"]
    duration_ms: int
    arguments_safe: dict[str, Any]
    result_summary: dict[str, Any]


class AgentRunResult(BaseModel):
    run_id: str
    status: Literal["completed", "failed"]
    model: str
    prompt_version: str
    final_response: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    tool_calls: list[ToolTrace] = Field(default_factory=list)


class AgentEvaluationCase(BaseModel):
    id: str
    question: str
    required_tools: list[str] = Field(default_factory=list)
    forbidden_tools: list[str] = Field(default_factory=list)
    min_tool_calls: int = Field(default=1, ge=0)


class AgentCaseEvaluation(BaseModel):
    case_id: str
    passed: bool
    status: str
    tool_sequence: list[str]
    missing_tools: list[str]
    forbidden_tools_used: list[str]
    reasons: list[str]


class AgentEvaluationReport(BaseModel):
    model: str
    prompt_version: str
    total: int
    passed: int
    pass_rate: float
    cases: list[AgentCaseEvaluation]
