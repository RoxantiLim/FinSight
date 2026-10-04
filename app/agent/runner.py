from __future__ import annotations

import json
import logging
import re
import time
from typing import Any
from uuid import uuid4

from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.agent.client import ModelClient
from app.agent.prompts import PROMPT_VERSION, SYSTEM_PROMPT
from app.agent.schemas import AgentRunResult, ToolTrace
from app.agent.tools import get_agent_tool_schemas
from app.analytics import execute_tool
from app.config import AgentSettings
from app.models import AgentRun, ToolCall, utc_now

LOGGER = logging.getLogger(__name__)
_SECRET_PATTERN = re.compile(r"sk-[A-Za-z0-9_-]{8,}")
_LONG_NUMBER_PATTERN = re.compile(r"(?<!\d)\d{12,19}(?!\d)")


def _safe_text(value: str, *, limit: int = 4000) -> str:
    redacted = _SECRET_PATTERN.sub("[REDACTED_KEY]", value)
    redacted = _LONG_NUMBER_PATTERN.sub("[REDACTED_NUMBER]", redacted)
    return redacted[:limit]


def _safe_value(value: Any) -> Any:
    if isinstance(value, str):
        return _safe_text(value, limit=500)
    if isinstance(value, dict):
        return {str(key): _safe_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_safe_value(item) for item in value[:100]]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return _safe_text(str(value), limit=500)


def _pick(payload: dict[str, Any], *keys: str) -> dict[str, Any]:
    return {key: payload[key] for key in keys if key in payload}


def _result_summary(tool_name: str, payload: dict[str, Any]) -> dict[str, Any]:
    if tool_name == "search_transactions":
        return _pick(
            payload,
            "matched_count",
            "returned_count",
            "total_amount_minor",
            "net_cashflow_minor",
            "truncated",
            "basis",
        )
    if tool_name == "get_monthly_summary":
        return _pick(
            payload,
            "requested_months",
            "months_with_data",
            "average_income_minor",
            "average_net_expense_minor",
            "average_balance_minor",
            "basis",
        )
    if tool_name == "compare_periods":
        summary = _pick(
            payload,
            "period_a",
            "period_b",
            "net_expense_change_minor",
            "net_expense_change_percent",
            "basis",
        )
        summary["top_category_changes"] = payload.get("category_changes", [])[:5]
        return summary
    if tool_name == "get_category_breakdown":
        summary = _pick(payload, "total_net_expense_minor", "truncated", "basis")
        summary["top_categories"] = payload.get("categories", [])[:5]
        return summary
    if tool_name == "find_recurring_expenses":
        items = payload.get("items", [])
        summary = _pick(payload, "period_start_month", "period_end_month", "basis")
        summary["item_count"] = len(items)
        summary["items"] = items[:5]
        return summary
    if tool_name == "find_abnormal_transactions":
        safe_items = []
        for item in payload.get("items", [])[:5]:
            safe_items.append(
                _pick(
                    item,
                    "occurred_at",
                    "merchant",
                    "category",
                    "amount_minor",
                    "baseline_median_minor",
                    "threshold_minor",
                    "anomaly_score",
                    "reason",
                    "is_one_off",
                )
            )
        return {
            "item_count": len(payload.get("items", [])),
            "items": safe_items,
            **_pick(payload, "strategy", "basis"),
        }
    if tool_name == "calculate_saving_gap":
        return dict(payload)
    if tool_name == "simulate_budget":
        summary = _pick(
            payload,
            "target_month",
            "baseline_period_start_month",
            "baseline_period_end_month",
            "months_with_data",
            "baseline_income_minor",
            "baseline_expense_minor",
            "baseline_balance_minor",
            "target_saving_minor",
            "required_reduction_minor",
            "planned_reduction_minor",
            "projected_expense_minor",
            "projected_balance_minor",
            "target_met",
            "warnings",
            "basis",
        )
        summary["items"] = payload.get("items", [])[:10]
        return summary
    return {"status": "result_available"}


class AgentRunner:
    def __init__(
        self,
        session: Session,
        *,
        user_id: str,
        model_client: ModelClient,
        settings: AgentSettings,
    ) -> None:
        self.session = session
        self.user_id = user_id
        self.model_client = model_client
        self.settings = settings

    def run(self, user_request: str) -> AgentRunResult:
        request = user_request.strip()
        if not request:
            raise ValueError("用户问题不能为空")

        run = AgentRun(
            id=str(uuid4()),
            user_id=self.user_id,
            user_request_safe=_safe_text(request),
            status="running",
            model=self.settings.model,
            prompt_version=PROMPT_VERSION,
        )
        self.session.add(run)
        self.session.commit()

        traces: list[ToolTrace] = []
        input_items: list[dict[str, Any]] = [{"role": "user", "content": request}]
        tools = get_agent_tool_schemas()
        started = time.monotonic()
        tool_call_count = 0
        consecutive_errors = 0
        last_signature: str | None = None

        while True:
            elapsed = time.monotonic() - started
            if elapsed >= self.settings.timeout_seconds:
                return self._fail(
                    run,
                    traces,
                    "agent_timeout",
                    f"Agent 运行超过 {self.settings.timeout_seconds:g} 秒，已安全停止。",
                )
            try:
                turn = self.model_client.create_response(
                    input_items=input_items,
                    tools=tools,
                    instructions=SYSTEM_PROMPT,
                    timeout_seconds=min(
                        self.settings.request_timeout_seconds,
                        self.settings.timeout_seconds - elapsed,
                    ),
                )
            except Exception as exc:  # provider exceptions share no stable base class
                LOGGER.warning("Model request failed: %s", type(exc).__name__)
                if time.monotonic() - started >= self.settings.timeout_seconds:
                    return self._fail(
                        run,
                        traces,
                        "agent_timeout",
                        f"Agent 运行超过 {self.settings.timeout_seconds:g} 秒，已安全停止。",
                    )
                return self._fail(
                    run,
                    traces,
                    "model_request_failed",
                    f"模型服务调用失败（{type(exc).__name__}），请检查密钥、网络或服务状态。",
                )

            if time.monotonic() - started >= self.settings.timeout_seconds:
                return self._fail(
                    run,
                    traces,
                    "agent_timeout",
                    f"Agent 运行超过 {self.settings.timeout_seconds:g} 秒，已安全停止。",
                )

            input_items.extend(turn.output_items)
            function_calls = [
                item for item in turn.output_items if item.get("type") == "function_call"
            ]
            if not function_calls:
                final_text = turn.output_text.strip()
                if not final_text:
                    return self._fail(
                        run,
                        traces,
                        "empty_model_response",
                        "模型没有返回最终回答或工具调用，已安全停止。",
                    )
                run.status = "completed"
                run.completed_at = utc_now()
                run.final_response_safe = _safe_text(final_text, limit=12000)
                self.session.commit()
                return AgentRunResult(
                    run_id=run.id,
                    status="completed",
                    model=run.model,
                    prompt_version=run.prompt_version,
                    final_response=final_text,
                    tool_calls=traces,
                )

            for call in function_calls:
                if tool_call_count >= self.settings.max_tool_calls:
                    return self._fail(
                        run,
                        traces,
                        "tool_call_limit_exceeded",
                        f"工具调用达到上限 {self.settings.max_tool_calls} 次，已安全停止。",
                    )
                tool_call_count += 1
                name = str(call.get("name", ""))
                call_id = str(call.get("call_id", ""))
                raw_arguments = call.get("arguments", "{}")
                call_started = time.monotonic()
                status = "succeeded"
                arguments: dict[str, Any] = {}
                output_payload: dict[str, Any]
                summary: dict[str, Any]

                try:
                    decoded = (
                        json.loads(raw_arguments)
                        if isinstance(raw_arguments, str)
                        else raw_arguments
                    )
                    if not isinstance(decoded, dict):
                        raise ValueError("工具参数必须是 JSON object")
                    arguments = decoded
                    signature = json.dumps(
                        [name, arguments], ensure_ascii=False, sort_keys=True, separators=(",", ":")
                    )
                    if signature == last_signature:
                        raise RuntimeError("duplicate_tool_call")
                    last_signature = signature
                    if not call_id:
                        raise ValueError("模型工具调用缺少 call_id")
                    result = execute_tool(
                        self.session,
                        user_id=self.user_id,
                        tool_name=name,
                        arguments=arguments,
                    )
                    result_payload = result.model_dump(mode="json")
                    output_payload = {"ok": True, "result": result_payload}
                    summary = _result_summary(name, result_payload)
                    consecutive_errors = 0
                except json.JSONDecodeError:
                    status = "failed"
                    consecutive_errors += 1
                    output_payload = {
                        "ok": False,
                        "error": {"code": "invalid_json", "message": "工具参数不是有效 JSON"},
                    }
                    summary = output_payload["error"]
                except ValidationError as exc:
                    status = "failed"
                    consecutive_errors += 1
                    output_payload = {
                        "ok": False,
                        "error": {
                            "code": "invalid_arguments",
                            "message": "工具参数校验失败",
                            "details": exc.errors(include_input=False),
                        },
                    }
                    summary = output_payload["error"]
                except RuntimeError as exc:
                    status = "failed"
                    consecutive_errors += 1
                    code = str(exc)
                    output_payload = {
                        "ok": False,
                        "error": {
                            "code": code,
                            "message": "检测到连续重复的相同工具调用，请改用已有结果或调整参数。",
                        },
                    }
                    summary = output_payload["error"]
                except ValueError as exc:
                    status = "failed"
                    consecutive_errors += 1
                    output_payload = {
                        "ok": False,
                        "error": {
                            "code": "tool_error",
                            "message": _safe_text(str(exc), limit=500),
                        },
                    }
                    summary = output_payload["error"]
                except Exception as exc:
                    status = "failed"
                    consecutive_errors += 1
                    LOGGER.warning("Tool %s failed: %s", name, type(exc).__name__)
                    output_payload = {
                        "ok": False,
                        "error": {
                            "code": "tool_execution_failed",
                            "message": f"工具执行失败（{type(exc).__name__}）",
                        },
                    }
                    summary = output_payload["error"]

                duration_ms = max(round((time.monotonic() - call_started) * 1000), 0)
                trace = ToolTrace(
                    sequence_no=tool_call_count,
                    tool_name=name or "unknown",
                    status=status,
                    duration_ms=duration_ms,
                    arguments_safe=_safe_value(arguments),
                    result_summary=_safe_value(summary),
                )
                traces.append(trace)
                self.session.add(
                    ToolCall(
                        id=str(uuid4()),
                        agent_run_id=run.id,
                        sequence_no=trace.sequence_no,
                        tool_name=trace.tool_name,
                        arguments_safe=trace.arguments_safe,
                        result_summary=trace.result_summary,
                        status=trace.status,
                        duration_ms=trace.duration_ms,
                    )
                )
                self.session.commit()

                if call_id:
                    input_items.append(
                        {
                            "type": "function_call_output",
                            "call_id": call_id,
                            "output": json.dumps(
                                output_payload, ensure_ascii=False, separators=(",", ":")
                            ),
                        }
                    )
                if consecutive_errors >= self.settings.max_consecutive_errors:
                    return self._fail(
                        run,
                        traces,
                        "too_many_tool_errors",
                        "连续工具错误达到上限，已安全停止。请调整问题或检查模型参数。",
                    )
                if time.monotonic() - started >= self.settings.timeout_seconds:
                    return self._fail(
                        run,
                        traces,
                        "agent_timeout",
                        f"Agent 运行超过 {self.settings.timeout_seconds:g} 秒，已安全停止。",
                    )

    def _fail(
        self,
        run: AgentRun,
        traces: list[ToolTrace],
        error_code: str,
        message: str,
    ) -> AgentRunResult:
        run.status = "failed"
        run.completed_at = utc_now()
        run.error_code = error_code
        run.final_response_safe = _safe_text(message)
        self.session.commit()
        return AgentRunResult(
            run_id=run.id,
            status="failed",
            model=run.model,
            prompt_version=run.prompt_version,
            error_code=error_code,
            error_message=message,
            tool_calls=traces,
        )
