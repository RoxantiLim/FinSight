from __future__ import annotations

from typing import Any

from app.analytics import TOOL_NAMES, get_tool_input_schema

TOOL_DESCRIPTIONS = {
    "search_transactions": (
        "按日期、分类、商户、交易类型和金额筛选交易摘要。仅在需要核查具体明细时使用；"
        "不要用它探测数据日期范围或验证转账口径。"
    ),
    "get_monthly_summary": "计算月份范围内逐月收入、净支出、结余及期间平均值。",
    "compare_periods": "比较两个日期区间的净支出变化和主要贡献分类。",
    "get_category_breakdown": (
        "按分类汇总指定日期范围的净支出，并区分固定、可变、周期性和一次性金额。"
    ),
    "find_recurring_expenses": "识别重复扣款和可能需要复核的订阅候选，不代表一定可取消。",
    "find_abnormal_transactions": "按个人历史基线找出异常金额，并返回阈值和异常原因。",
    "calculate_saving_gap": (
        "根据最近若干完整月份的平均结余，精确计算目标储蓄缺口。省略 as_of_month 时自动"
        "使用数据库最新月份；返回值包含后续分类调查应使用的起止月份。"
    ),
    "simulate_budget": "确定性模拟分类预算或削减方案，验证目标储蓄是否能够达成。",
}


def get_agent_tool_schemas() -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "name": name,
            "description": TOOL_DESCRIPTIONS[name],
            "parameters": get_tool_input_schema(name),
        }
        for name in TOOL_NAMES
    ]
