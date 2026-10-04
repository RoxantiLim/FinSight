from __future__ import annotations

import json
from pathlib import Path

from app.agent.prompts import PROMPT_VERSION
from app.agent.schemas import (
    AgentCaseEvaluation,
    AgentEvaluationCase,
    AgentEvaluationReport,
    AgentRunResult,
)


def load_evaluation_cases(path: str | Path) -> list[AgentEvaluationCase]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError("Agent evaluation case file must contain a JSON array")
    return [AgentEvaluationCase.model_validate(item) for item in payload]


def evaluate_case(case: AgentEvaluationCase, result: AgentRunResult) -> AgentCaseEvaluation:
    sequence = [item.tool_name for item in result.tool_calls if item.status == "succeeded"]
    used = set(sequence)
    missing = sorted(set(case.required_tools) - used)
    forbidden = sorted(set(case.forbidden_tools) & used)
    reasons: list[str] = []
    if result.status != "completed":
        reasons.append(f"agent status is {result.status}: {result.error_code or 'unknown'}")
    if missing:
        reasons.append(f"missing required tools: {', '.join(missing)}")
    if forbidden:
        reasons.append(f"used forbidden tools: {', '.join(forbidden)}")
    if len(sequence) < case.min_tool_calls:
        reasons.append(
            f"only {len(sequence)} successful tool calls; expected at least {case.min_tool_calls}"
        )
    return AgentCaseEvaluation(
        case_id=case.id,
        passed=not reasons,
        status=result.status,
        tool_sequence=sequence,
        missing_tools=missing,
        forbidden_tools_used=forbidden,
        reasons=reasons,
    )


def build_evaluation_report(
    *, model: str, results: list[AgentCaseEvaluation]
) -> AgentEvaluationReport:
    passed = sum(item.passed for item in results)
    total = len(results)
    return AgentEvaluationReport(
        model=model,
        prompt_version=PROMPT_VERSION,
        total=total,
        passed=passed,
        pass_rate=round(passed / total * 100, 2) if total else 0.0,
        cases=results,
    )
