from __future__ import annotations

from collections.abc import Callable
from typing import Any

from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.analytics.schemas import (
    AbnormalTransactionsInput,
    CategoryBreakdownInput,
    ComparePeriodsInput,
    MonthlySummaryInput,
    RecurringExpensesInput,
    SavingGapInput,
    SearchTransactionsInput,
    SimulateBudgetInput,
)
from app.analytics.service import FinancialAnalyticsService

ToolInput = type[BaseModel]

_TOOL_INPUTS: dict[str, ToolInput] = {
    "search_transactions": SearchTransactionsInput,
    "get_monthly_summary": MonthlySummaryInput,
    "compare_periods": ComparePeriodsInput,
    "get_category_breakdown": CategoryBreakdownInput,
    "find_recurring_expenses": RecurringExpensesInput,
    "find_abnormal_transactions": AbnormalTransactionsInput,
    "calculate_saving_gap": SavingGapInput,
    "simulate_budget": SimulateBudgetInput,
}

TOOL_NAMES = tuple(_TOOL_INPUTS)


def get_tool_input_schema(tool_name: str) -> dict[str, Any]:
    try:
        model = _TOOL_INPUTS[tool_name]
    except KeyError as exc:
        raise ValueError(f"unknown tool: {tool_name}") from exc
    return model.model_json_schema()


def execute_tool(
    session: Session,
    *,
    user_id: str,
    tool_name: str,
    arguments: dict[str, Any],
) -> BaseModel:
    try:
        input_model = _TOOL_INPUTS[tool_name]
    except KeyError as exc:
        raise ValueError(f"unknown tool: {tool_name}") from exc
    params = input_model.model_validate(arguments)
    service = FinancialAnalyticsService(session, user_id)
    method: Callable[[Any], BaseModel] = getattr(service, tool_name)
    return method(params)
