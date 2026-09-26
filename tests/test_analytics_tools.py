from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.analytics import TOOL_NAMES, execute_tool, get_tool_input_schema
from app.analytics.schemas import (
    AbnormalTransactionsInput,
    CategoryBreakdownInput,
    ComparePeriodsInput,
    MonthlySummaryInput,
    PeriodDefinition,
    RecurringExpensesInput,
    SavingGapInput,
    SearchTransactionsInput,
    SimulateBudgetInput,
)
from app.analytics.service import FinancialAnalyticsService
from app.db import create_db_engine, create_schema, create_session_factory
from app.demo.generator import generate_demo_dataset
from app.importers import AlipayAdapter, WechatAdapter
from app.services.import_service import import_bill


@pytest.fixture
def analytics_context(tmp_path: Path):
    demo_dir = tmp_path / "demo"
    truth = generate_demo_dataset(demo_dir)
    engine = create_db_engine(tmp_path / "analytics.db")
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
        yield FinancialAnalyticsService(session, "demo-user"), truth, session


def test_registry_exposes_all_eight_tools(analytics_context) -> None:
    _, truth, session = analytics_context
    assert len(TOOL_NAMES) == 8
    assert "properties" in get_tool_input_schema("calculate_saving_gap")
    result = execute_tool(
        session,
        user_id="demo-user",
        tool_name="calculate_saving_gap",
        arguments={"target_amount_minor": 200_000, "lookback_months": 3},
    )
    assert result.saving_gap_minor == truth["saving_gap_minor"]


def test_monthly_summary_and_saving_gap_match_ground_truth(analytics_context) -> None:
    service, truth, _ = analytics_context
    summary = service.get_monthly_summary(
        MonthlySummaryInput(start_month="2026-03", end_month="2026-08")
    )

    assert summary.requested_months == 6
    assert summary.months_with_data == 6
    for month in summary.months:
        expected = truth["monthly_summary"][month.month]
        assert month.income_minor == expected["income_minor"]
        assert month.gross_expense_minor == expected["gross_expense_minor"]
        assert month.refund_minor == expected["refund_minor"]
        assert month.net_expense_minor == expected["net_expense_minor"]
        assert month.balance_minor == expected["balance_minor"]
        assert month.transfer_minor_excluded == expected["transfer_minor_excluded"]

    gap = service.calculate_saving_gap(
        SavingGapInput(target_amount_minor=200_000, lookback_months=3)
    )
    assert gap.period_start_month == "2026-06"
    assert gap.period_end_month == "2026-08"
    assert gap.average_balance_minor == truth["recent_three_month_average_balance_minor"]
    assert gap.saving_gap_minor == truth["saving_gap_minor"]
    assert gap.data_sufficient


def test_search_respects_filters_limits_and_safe_output(analytics_context) -> None:
    service, _, _ = analytics_context
    result = service.search_transactions(
        SearchTransactionsInput(
            start_date=date(2026, 8, 22),
            end_date=date(2026, 8, 22),
            merchant_query="数码",
            transaction_types=["expense"],
            min_amount_minor=150_000,
            limit=1,
        )
    )

    assert result.matched_count == 1
    assert result.transactions[0].amount_minor == 150_000
    fields = result.transactions[0].model_dump()
    assert "source_transaction_id" not in fields
    assert "dedup_key" not in fields
    assert "description_safe" not in fields

    limited = service.search_transactions(SearchTransactionsInput(categories=["餐饮"], limit=2))
    assert limited.returned_count == 2
    assert limited.matched_count > limited.returned_count
    assert limited.truncated


def test_period_comparison_and_category_breakdown_handle_one_offs(analytics_context) -> None:
    service, truth, _ = analytics_context
    periods = ComparePeriodsInput(
        period_a=PeriodDefinition(start_date=date(2026, 7, 1), end_date=date(2026, 7, 31)),
        period_b=PeriodDefinition(start_date=date(2026, 8, 1), end_date=date(2026, 8, 31)),
    )
    comparison = service.compare_periods(periods)
    expected_change = truth["latest_month_comparison"]["net_expense_change_minor"]
    assert comparison.net_expense_change_minor == expected_change
    assert comparison.category_changes[0].category == "购物"

    without_one_off = service.compare_periods(periods.model_copy(update={"exclude_one_off": True}))
    assert (
        comparison.period_b.net_expense_minor - without_one_off.period_b.net_expense_minor
        == 150_000
    )

    breakdown = service.get_category_breakdown(
        CategoryBreakdownInput(
            start_date=date(2026, 6, 1),
            end_date=date(2026, 8, 31),
            top_n=50,
        )
    )
    expected_total = sum(
        truth["monthly_summary"][month]["net_expense_minor"]
        for month in ("2026-06", "2026-07", "2026-08")
    )
    assert breakdown.total_net_expense_minor == expected_total
    assert sum(item.net_expense_minor for item in breakdown.categories) == expected_total
    assert {item.category: item.net_expense_minor for item in breakdown.categories} == truth[
        "recent_three_month_category_net_expense_minor"
    ]


def test_recurring_and_abnormal_tools_find_seeded_patterns(analytics_context) -> None:
    service, truth, _ = analytics_context
    recurring = service.find_recurring_expenses(
        RecurringExpensesInput(lookback_months=6, min_occurrences=3)
    )
    subscription_merchants = {
        item.merchant for item in recurring.items if item.cancellable_candidate
    }
    assert {item.merchant for item in recurring.items} == set(truth["known_recurring_merchants"])
    assert subscription_merchants == {"云音乐", "视频空间", "效率工具"}
    rent = next(item for item in recurring.items if item.merchant == "安心公寓")
    assert rent.estimated_period == "monthly"
    assert not rent.cancellable_candidate

    abnormal = service.find_abnormal_transactions(
        AbnormalTransactionsInput(
            start_date=date(2026, 8, 1),
            end_date=date(2026, 8, 31),
            max_results=5,
        )
    )
    assert abnormal.items
    assert abnormal.items[0].merchant == "数码体验店"
    assert abnormal.items[0].amount_minor == 150_000
    assert abnormal.items[0].is_one_off


def test_budget_simulation_proves_whether_plan_covers_gap(analytics_context) -> None:
    service, truth, _ = analytics_context
    no_changes = service.simulate_budget(
        SimulateBudgetInput(target_month="2026-09", target_saving_minor=200_000)
    )
    assert not no_changes.target_met
    assert no_changes.required_reduction_minor == truth["saving_gap_minor"]

    plan = service.simulate_budget(
        SimulateBudgetInput(
            target_month="2026-09",
            target_saving_minor=200_000,
            category_reductions_minor={
                "餐饮": 40_000,
                "购物": 30_000,
                "娱乐": 15_000,
                "交通": 10_000,
            },
        )
    )
    assert plan.planned_reduction_minor == 95_000
    assert plan.projected_balance_minor == plan.baseline_balance_minor + 95_000
    assert plan.target_met


def test_empty_data_and_invalid_parameters_are_explicit(tmp_path: Path) -> None:
    engine = create_db_engine(tmp_path / "empty.db")
    create_schema(engine)
    session_factory = create_session_factory(engine)
    with session_factory() as session:
        service = FinancialAnalyticsService(session, "missing-user")
        gap = service.calculate_saving_gap(
            SavingGapInput(target_amount_minor=200_000, lookback_months=3)
        )
        assert gap.months_with_data == 0
        assert gap.saving_gap_minor == 200_000
        assert not gap.data_sufficient

    with pytest.raises(ValidationError):
        MonthlySummaryInput(start_month="2026-08", end_month="2026-06")
    with pytest.raises(ValidationError):
        SearchTransactionsInput(min_amount_minor=200, max_amount_minor=100)
    with pytest.raises(ValidationError):
        SimulateBudgetInput(
            target_month="2026-09",
            target_saving_minor=200_000,
            category_budgets_minor={"餐饮": 50_000},
            category_reductions_minor={"餐饮": 10_000},
        )


def test_tool_output_is_json_serializable(analytics_context) -> None:
    service, _, _ = analytics_context
    output = service.calculate_saving_gap(
        SavingGapInput(target_amount_minor=200_000, lookback_months=3)
    )
    json.dumps(output.model_dump(mode="json"), ensure_ascii=False)
