from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import SecretStr
from sqlalchemy import select

from app.agent import AgentRunner, ModelTurn
from app.agent.evaluation import evaluate_case, load_evaluation_cases
from app.agent.schemas import AgentEvaluationCase, AgentRunResult, ToolTrace
from app.agent.tools import get_agent_tool_schemas
from app.config import AgentSettings
from app.db import create_db_engine, create_schema, create_session_factory
from app.demo.generator import generate_demo_dataset
from app.importers import AlipayAdapter, WechatAdapter
from app.models import AgentRun, ToolCall
from app.services.import_service import import_bill


class ScriptedModelClient:
    def __init__(self, turns: list[ModelTurn]) -> None:
        self.turns = list(turns)
        self.inputs: list[list[dict[str, Any]]] = []

    def create_response(
        self,
        *,
        input_items: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        instructions: str,
        timeout_seconds: float,
    ) -> ModelTurn:
        self.inputs.append(list(input_items))
        assert len(tools) == 8
        assert "金额计算必须来自工具" in instructions
        assert timeout_seconds > 0
        if not self.turns:
            raise AssertionError("scripted model ran out of turns")
        return self.turns.pop(0)


def tool_turn(call_id: str, name: str, arguments: dict[str, Any]) -> ModelTurn:
    return ModelTurn(
        response_id=f"response-{call_id}",
        output_items=[
            {
                "type": "function_call",
                "call_id": call_id,
                "name": name,
                "arguments": json.dumps(arguments, ensure_ascii=False),
            }
        ],
    )


def final_turn(text: str = "已根据工具结果完成分析。") -> ModelTurn:
    return ModelTurn(
        response_id="response-final",
        output_items=[
            {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": text}],
            }
        ],
        output_text=text,
    )


def settings(**updates: Any) -> AgentSettings:
    values: dict[str, Any] = {
        "api_key": SecretStr("test-key"),
        "model": "scripted-model",
        "reasoning_effort": "none",
        "timeout_seconds": 10,
        "request_timeout_seconds": 5,
    }
    values.update(updates)
    return AgentSettings.model_validate(values)


@pytest.fixture
def agent_context(tmp_path: Path):
    demo_dir = tmp_path / "demo"
    generate_demo_dataset(demo_dir)
    engine = create_db_engine(tmp_path / "agent.db")
    create_schema(engine)
    session_factory = create_session_factory(engine)
    with session_factory() as session:
        import_bill(
            session,
            adapter=WechatAdapter(),
            file_path=demo_dir / "demo_wechat_bill.csv",
            user_id="demo-user",
        )
        import_bill(
            session,
            adapter=AlipayAdapter(),
            file_path=demo_dir / "demo_alipay_bill.csv",
            user_id="demo-user",
        )
        yield session


def test_agent_tool_schemas_register_all_read_only_tools() -> None:
    schemas = get_agent_tool_schemas()

    assert len(schemas) == 8
    assert {item["name"] for item in schemas} == {
        "search_transactions",
        "get_monthly_summary",
        "compare_periods",
        "get_category_breakdown",
        "find_recurring_expenses",
        "find_abnormal_transactions",
        "calculate_saving_gap",
        "simulate_budget",
    }
    assert all(item["type"] == "function" for item in schemas)
    assert all(item["description"] for item in schemas)
    assert all(item["parameters"].get("additionalProperties") is False for item in schemas)


def test_agent_runs_model_driven_multistep_path_and_persists_trace(agent_context) -> None:
    client = ScriptedModelClient(
        [
            tool_turn(
                "call-gap",
                "calculate_saving_gap",
                {"target_amount_minor": 200_000, "lookback_months": 3},
            ),
            tool_turn(
                "call-categories",
                "get_category_breakdown",
                {"start_date": "2026-06-01", "end_date": "2026-08-31"},
            ),
            tool_turn(
                "call-budget",
                "simulate_budget",
                {
                    "target_month": "2026-09",
                    "target_saving_minor": 200_000,
                    "category_reductions_minor": {
                        "餐饮": 40_000,
                        "购物": 30_000,
                        "娱乐": 15_000,
                        "交通": 10_000,
                    },
                },
            ),
            final_turn("预算模拟已验证目标可达成。"),
        ]
    )
    runner = AgentRunner(
        agent_context,
        user_id="demo-user",
        model_client=client,
        settings=settings(),
    )

    result = runner.run("我下个月想存下2000元，请根据最近三个月消费制定计划。")

    assert result.status == "completed"
    assert [item.tool_name for item in result.tool_calls] == [
        "calculate_saving_gap",
        "get_category_breakdown",
        "simulate_budget",
    ]
    assert result.tool_calls[-1].result_summary["target_met"] is True
    assert any(
        item.get("type") == "function_call_output" for item in client.inputs[-1]
    )
    stored_run = agent_context.scalar(select(AgentRun).where(AgentRun.id == result.run_id))
    stored_calls = list(
        agent_context.scalars(
            select(ToolCall)
            .where(ToolCall.agent_run_id == result.run_id)
            .order_by(ToolCall.sequence_no)
        )
    )
    assert stored_run is not None
    assert stored_run.status == "completed"
    assert stored_run.prompt_version == "m3-v2"
    assert len(stored_calls) == 3
    assert all(item.status == "succeeded" for item in stored_calls)


def test_different_question_uses_different_tool_path(agent_context) -> None:
    client = ScriptedModelClient(
        [
            tool_turn(
                "call-recurring",
                "find_recurring_expenses",
                {"lookback_months": 6, "min_occurrences": 3},
            ),
            final_turn(),
        ]
    )
    result = AgentRunner(
        agent_context,
        user_id="demo-user",
        model_client=client,
        settings=settings(),
    ).run("我有哪些可能可以取消的周期性支出？")

    assert result.status == "completed"
    assert [item.tool_name for item in result.tool_calls] == ["find_recurring_expenses"]


def test_invalid_and_duplicate_calls_stop_after_error_limit(agent_context) -> None:
    invalid = {"target_amount_minor": -1, "lookback_months": 3}
    client = ScriptedModelClient(
        [
            tool_turn("call-invalid", "calculate_saving_gap", invalid),
            tool_turn("call-duplicate", "calculate_saving_gap", invalid),
        ]
    )
    result = AgentRunner(
        agent_context,
        user_id="demo-user",
        model_client=client,
        settings=settings(max_consecutive_errors=2),
    ).run("制定一个储蓄计划")

    assert result.status == "failed"
    assert result.error_code == "too_many_tool_errors"
    assert [item.status for item in result.tool_calls] == ["failed", "failed"]
    assert result.tool_calls[0].result_summary["code"] == "invalid_arguments"
    assert result.tool_calls[1].result_summary["code"] == "duplicate_tool_call"


def test_tool_call_limit_stops_loop(agent_context) -> None:
    client = ScriptedModelClient(
        [
            tool_turn(
                "call-one",
                "find_recurring_expenses",
                {"lookback_months": 6},
            ),
            tool_turn(
                "call-two",
                "find_abnormal_transactions",
                {"start_date": "2026-08-01", "end_date": "2026-08-31"},
            ),
        ]
    )
    result = AgentRunner(
        agent_context,
        user_id="demo-user",
        model_client=client,
        settings=settings(max_tool_calls=1),
    ).run("检查周期和异常支出")

    assert result.status == "failed"
    assert result.error_code == "tool_call_limit_exceeded"
    assert len(result.tool_calls) == 1


def test_total_timeout_stops_after_model_request(agent_context, monkeypatch) -> None:
    ticks = iter([0.0, 0.0, 2.0])
    monkeypatch.setattr("app.agent.runner.time.monotonic", lambda: next(ticks))
    client = ScriptedModelClient([final_turn()])

    result = AgentRunner(
        agent_context,
        user_id="demo-user",
        model_client=client,
        settings=settings(timeout_seconds=1),
    ).run("分析最近消费")

    assert result.status == "failed"
    assert result.error_code == "agent_timeout"


def test_fixed_evaluation_cases_are_machine_readable() -> None:
    cases = load_evaluation_cases("data/demo/agent_eval_cases.json")

    assert [case.id for case in cases] == ["E01", "E02", "E03", "E04", "E05"]
    evaluation = evaluate_case(
        AgentEvaluationCase(
            id="sample",
            question="sample",
            required_tools=["find_recurring_expenses"],
        ),
        AgentRunResult(
            run_id="run-sample",
            status="completed",
            model="scripted-model",
            prompt_version="m3-v2",
            final_response="done",
            tool_calls=[
                ToolTrace(
                    sequence_no=1,
                    tool_name="find_recurring_expenses",
                    status="succeeded",
                    duration_ms=1,
                    arguments_safe={},
                    result_summary={},
                )
            ],
        ),
    )
    assert evaluation.passed
