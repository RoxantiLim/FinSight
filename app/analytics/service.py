from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, time, timedelta
from statistics import median

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from app.analytics.schemas import (
    AbnormalTransaction,
    AbnormalTransactionsInput,
    AbnormalTransactionsOutput,
    BudgetSimulationItem,
    CategoryAmount,
    CategoryBreakdownInput,
    CategoryBreakdownOutput,
    CategoryChange,
    ComparePeriodsInput,
    ComparePeriodsOutput,
    MonthlyAmount,
    MonthlySummaryInput,
    MonthlySummaryOutput,
    PeriodDefinition,
    PeriodTotals,
    RecurringExpense,
    RecurringExpensesInput,
    RecurringExpensesOutput,
    SavingGapInput,
    SavingGapOutput,
    SearchTransactionsInput,
    SearchTransactionsOutput,
    SimulateBudgetInput,
    SimulateBudgetOutput,
    TransactionSummary,
)
from app.models import Transaction


def _month_start(month: str) -> date:
    return date.fromisoformat(f"{month}-01")


def _shift_month(value: date, offset: int) -> date:
    index = value.year * 12 + value.month - 1 + offset
    return date(index // 12, index % 12 + 1, 1)


def _month_keys(start_month: str, end_month: str) -> list[str]:
    current = _month_start(start_month)
    end = _month_start(end_month)
    result: list[str] = []
    while current <= end:
        result.append(current.strftime("%Y-%m"))
        current = _shift_month(current, 1)
    return result


def _day_start(value: date) -> datetime:
    return datetime.combine(value, time.min)


def _next_day_start(value: date) -> datetime:
    return _day_start(value + timedelta(days=1))


def _percentile(values: list[int], fraction: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    position = (len(ordered) - 1) * fraction
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def _allocate_average(totals: dict[str, int], divisor: int) -> dict[str, int]:
    if not totals:
        return {}
    averages = {category: round(amount / divisor) for category, amount in totals.items()}
    target_total = round(sum(totals.values()) / divisor)
    difference = target_total - sum(averages.values())
    if difference == 0:
        return averages
    residuals = {category: totals[category] / divisor - averages[category] for category in totals}
    ordered = sorted(residuals, key=residuals.get, reverse=difference > 0)
    step = 1 if difference > 0 else -1
    for index in range(abs(difference)):
        category = ordered[index % len(ordered)]
        averages[category] += step
    return averages


class FinancialAnalyticsService:
    def __init__(self, session: Session, user_id: str) -> None:
        self.session = session
        self.user_id = user_id

    def _base_query(self) -> Select[tuple[Transaction]]:
        return select(Transaction).where(Transaction.user_id == self.user_id)

    def _records_for_dates(self, start_date: date, end_date: date) -> list[Transaction]:
        query = (
            self._base_query()
            .where(
                Transaction.occurred_at >= _day_start(start_date),
                Transaction.occurred_at < _next_day_start(end_date),
            )
            .order_by(Transaction.occurred_at)
        )
        return list(self.session.scalars(query))

    def _latest_month(self) -> str | None:
        latest = self.session.scalar(
            select(func.max(Transaction.occurred_at)).where(Transaction.user_id == self.user_id)
        )
        return latest.strftime("%Y-%m") if latest else None

    def _lookback_dates(
        self, lookback_months: int, as_of_month: str | None
    ) -> tuple[date, date, str, str] | None:
        end_month = as_of_month or self._latest_month()
        if end_month is None:
            return None
        end_start = _month_start(end_month)
        start = _shift_month(end_start, -(lookback_months - 1))
        end_exclusive = _shift_month(end_start, 1)
        return start, end_exclusive - timedelta(days=1), start.strftime("%Y-%m"), end_month

    @staticmethod
    def _filtered_financial_records(
        records: list[Transaction], *, exclude_one_off: bool
    ) -> list[Transaction]:
        if not exclude_one_off:
            return records
        return [item for item in records if not item.is_one_off]

    @staticmethod
    def _period_totals(records: list[Transaction]) -> PeriodTotals:
        income = sum(item.amount_minor for item in records if item.transaction_type == "income")
        expense = sum(item.amount_minor for item in records if item.transaction_type == "expense")
        refunds = sum(item.amount_minor for item in records if item.transaction_type == "refund")
        net_expense = expense - refunds
        return PeriodTotals(
            income_minor=income,
            gross_expense_minor=expense,
            refund_minor=refunds,
            net_expense_minor=net_expense,
            balance_minor=income - net_expense,
            transaction_count=len(records),
        )

    @staticmethod
    def _category_net(records: list[Transaction]) -> dict[str, int]:
        totals: dict[str, int] = defaultdict(int)
        for item in records:
            if item.transaction_type == "expense":
                totals[item.category] += item.amount_minor
            elif item.transaction_type == "refund":
                totals[item.category] -= item.amount_minor
        return dict(totals)

    def search_transactions(self, params: SearchTransactionsInput) -> SearchTransactionsOutput:
        query = self._base_query()
        if params.start_date:
            query = query.where(Transaction.occurred_at >= _day_start(params.start_date))
        if params.end_date:
            query = query.where(Transaction.occurred_at < _next_day_start(params.end_date))
        if params.categories:
            query = query.where(Transaction.category.in_(params.categories))
        if params.merchant_query:
            query = query.where(Transaction.merchant_normalized.contains(params.merchant_query))
        if params.transaction_types:
            query = query.where(Transaction.transaction_type.in_(params.transaction_types))
        if params.min_amount_minor is not None:
            query = query.where(Transaction.amount_minor >= params.min_amount_minor)
        if params.max_amount_minor is not None:
            query = query.where(Transaction.amount_minor <= params.max_amount_minor)

        matched = list(self.session.scalars(query.order_by(Transaction.occurred_at.desc())))
        returned = matched[: params.limit]
        net_effect = 0
        for item in matched:
            if item.transaction_type in {"income", "refund"}:
                net_effect += item.amount_minor
            elif item.transaction_type == "expense":
                net_effect -= item.amount_minor
        return SearchTransactionsOutput(
            matched_count=len(matched),
            returned_count=len(returned),
            total_amount_minor=sum(item.amount_minor for item in matched),
            net_cashflow_minor=net_effect,
            truncated=len(matched) > len(returned),
            transactions=[
                TransactionSummary(
                    transaction_id=item.id,
                    occurred_at=item.occurred_at,
                    transaction_type=item.transaction_type,
                    amount_minor=item.amount_minor,
                    merchant=item.merchant_normalized,
                    category=item.category,
                    expense_nature=item.expense_nature,
                    is_recurring=item.is_recurring,
                    is_one_off=item.is_one_off,
                )
                for item in returned
            ],
            basis="Amounts are integer minor units; transfers have zero net cashflow effect.",
        )

    def get_monthly_summary(self, params: MonthlySummaryInput) -> MonthlySummaryOutput:
        keys = _month_keys(params.start_month, params.end_month)
        start = _month_start(params.start_month)
        end_exclusive = _shift_month(_month_start(params.end_month), 1)
        records = self._records_for_dates(start, end_exclusive - timedelta(days=1))
        records = self._filtered_financial_records(records, exclude_one_off=params.exclude_one_off)
        grouped: dict[str, list[Transaction]] = defaultdict(list)
        for item in records:
            grouped[item.occurred_at.strftime("%Y-%m")].append(item)

        months: list[MonthlyAmount] = []
        for key in keys:
            month_records = grouped[key]
            totals = self._period_totals(month_records)
            transfers = sum(
                item.amount_minor for item in month_records if item.transaction_type == "transfer"
            )
            months.append(
                MonthlyAmount(
                    month=key,
                    **totals.model_dump(),
                    transfer_minor_excluded=transfers,
                )
            )
        populated = [item for item in months if item.transaction_count > 0]
        divisor = len(populated)

        def average(field: str) -> int:
            if divisor == 0:
                return 0
            return round(sum(getattr(item, field) for item in populated) / divisor)

        return MonthlySummaryOutput(
            months=months,
            requested_months=len(keys),
            months_with_data=divisor,
            average_income_minor=average("income_minor"),
            average_net_expense_minor=average("net_expense_minor"),
            average_balance_minor=average("balance_minor"),
            basis=(
                "Refunds reduce expenses; transfers are excluded; averages use months with data; "
                f"exclude_one_off={params.exclude_one_off}."
            ),
        )

    def _records_for_period(
        self,
        period: PeriodDefinition,
        *,
        categories: list[str],
        exclude_one_off: bool,
    ) -> list[Transaction]:
        records = self._records_for_dates(period.start_date, period.end_date)
        if categories:
            records = [item for item in records if item.category in categories]
        return self._filtered_financial_records(records, exclude_one_off=exclude_one_off)

    def compare_periods(self, params: ComparePeriodsInput) -> ComparePeriodsOutput:
        records_a = self._records_for_period(
            params.period_a,
            categories=params.categories,
            exclude_one_off=params.exclude_one_off,
        )
        records_b = self._records_for_period(
            params.period_b,
            categories=params.categories,
            exclude_one_off=params.exclude_one_off,
        )
        totals_a = self._period_totals(records_a)
        totals_b = self._period_totals(records_b)
        category_a = self._category_net(records_a)
        category_b = self._category_net(records_b)
        changes = [
            CategoryChange(
                category=category,
                period_a_minor=category_a.get(category, 0),
                period_b_minor=category_b.get(category, 0),
                change_minor=category_b.get(category, 0) - category_a.get(category, 0),
            )
            for category in set(category_a) | set(category_b)
        ]
        changes.sort(key=lambda item: abs(item.change_minor), reverse=True)
        change = totals_b.net_expense_minor - totals_a.net_expense_minor
        change_percent = (
            round(change / totals_a.net_expense_minor * 100, 2)
            if totals_a.net_expense_minor
            else None
        )
        return ComparePeriodsOutput(
            period_a=totals_a,
            period_b=totals_b,
            net_expense_change_minor=change,
            net_expense_change_percent=change_percent,
            category_changes=changes,
            basis=(
                "Net expense equals gross expense minus refunds; transfers are excluded; "
                f"exclude_one_off={params.exclude_one_off}."
            ),
        )

    def get_category_breakdown(self, params: CategoryBreakdownInput) -> CategoryBreakdownOutput:
        records = self._records_for_dates(params.start_date, params.end_date)
        if not params.include_one_off:
            records = [item for item in records if not item.is_one_off]
        buckets: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
        for item in records:
            if item.transaction_type == "expense":
                bucket = buckets[item.category]
                bucket["gross"] += item.amount_minor
                bucket[item.expense_nature] += item.amount_minor
                bucket["count"] += 1
                if item.is_recurring:
                    bucket["recurring"] += item.amount_minor
                if item.is_one_off:
                    bucket["one_off"] += item.amount_minor
            elif item.transaction_type == "refund":
                buckets[item.category]["refund"] += item.amount_minor

        net_by_category = {
            category: values["gross"] - values["refund"] for category, values in buckets.items()
        }
        total = sum(net_by_category.values())
        categories = [
            CategoryAmount(
                category=category,
                net_expense_minor=net,
                gross_expense_minor=buckets[category]["gross"],
                refund_minor=buckets[category]["refund"],
                fixed_minor=buckets[category]["fixed"],
                variable_minor=buckets[category]["variable"],
                recurring_minor=buckets[category]["recurring"],
                one_off_minor=buckets[category]["one_off"],
                expense_count=buckets[category]["count"],
                share_percent=round(net / total * 100, 2) if total else 0.0,
            )
            for category, net in net_by_category.items()
        ]
        categories.sort(key=lambda item: item.net_expense_minor, reverse=True)
        return CategoryBreakdownOutput(
            total_net_expense_minor=total,
            categories=categories[: params.top_n],
            truncated=len(categories) > params.top_n,
            basis=(
                "Only expenses and refunds are included; refunds reduce their category; "
                f"include_one_off={params.include_one_off}."
            ),
        )

    def find_recurring_expenses(self, params: RecurringExpensesInput) -> RecurringExpensesOutput:
        bounds = self._lookback_dates(params.lookback_months, params.as_of_month)
        if bounds is None:
            return RecurringExpensesOutput(
                period_start_month=None,
                period_end_month=None,
                items=[],
                basis="No transaction data is available.",
            )
        start, end, start_month, end_month = bounds
        records = [
            item
            for item in self._records_for_dates(start, end)
            if item.transaction_type == "expense" and item.is_recurring
        ]
        grouped: dict[tuple[str, str], list[Transaction]] = defaultdict(list)
        for item in records:
            grouped[(item.merchant_normalized, item.category)].append(item)

        items: list[RecurringExpense] = []
        for (merchant, category), matches in grouped.items():
            if len(matches) < params.min_occurrences:
                continue
            amounts = [item.amount_minor for item in matches]
            typical = round(median(amounts))
            tolerance = typical * params.amount_tolerance_percent / 100
            stable_count = sum(abs(value - typical) <= tolerance for value in amounts)
            active_months = len({item.occurred_at.strftime("%Y-%m") for item in matches})
            coverage = min(active_months / params.lookback_months, 1.0)
            stability = stable_count / len(matches)
            confidence = round(coverage * 0.7 + stability * 0.3, 2)
            cancellable = category == "订阅"
            items.append(
                RecurringExpense(
                    merchant=merchant,
                    category=category,
                    occurrences=len(matches),
                    active_months=active_months,
                    typical_amount_minor=typical,
                    latest_date=max(item.occurred_at.date() for item in matches),
                    estimated_period="monthly" if active_months >= 2 else "repeating",
                    confidence=confidence,
                    cancellable_candidate=cancellable,
                    candidate_label=(
                        "possible_subscription" if cancellable else "recurring_essential_or_unknown"
                    ),
                )
            )
        items.sort(
            key=lambda item: (item.cancellable_candidate, item.typical_amount_minor),
            reverse=True,
        )
        return RecurringExpensesOutput(
            period_start_month=start_month,
            period_end_month=end_month,
            items=items,
            basis=(
                "Candidates require repeated imported labels; cancellable means reviewable, "
                "not that the expense is unnecessary."
            ),
        )

    def find_abnormal_transactions(
        self, params: AbnormalTransactionsInput
    ) -> AbnormalTransactionsOutput:
        history = list(
            self.session.scalars(
                self._base_query().where(Transaction.transaction_type == "expense")
            )
        )
        if params.category:
            history = [item for item in history if item.category == params.category]
        target = [
            item
            for item in history
            if params.start_date <= item.occurred_at.date() <= params.end_date
        ]
        baseline_by_category: dict[str, list[int]] = defaultdict(list)
        for item in history:
            baseline_by_category[item.category].append(item.amount_minor)

        items: list[AbnormalTransaction] = []
        for item in target:
            values = baseline_by_category[item.category]
            if len(values) < 4:
                continue
            baseline_median = round(median(values))
            if params.threshold_strategy == "iqr":
                q1 = _percentile(values, 0.25)
                q3 = _percentile(values, 0.75)
                threshold = round(q3 + 1.5 * (q3 - q1))
                reason = "amount exceeds the category IQR upper fence"
            else:
                threshold = round(baseline_median * params.threshold_multiplier)
                reason = "amount exceeds the category median multiplier"
            if item.amount_minor <= threshold:
                continue
            score = round(item.amount_minor / max(threshold, 1), 2)
            items.append(
                AbnormalTransaction(
                    transaction_id=item.id,
                    occurred_at=item.occurred_at,
                    merchant=item.merchant_normalized,
                    category=item.category,
                    amount_minor=item.amount_minor,
                    baseline_median_minor=baseline_median,
                    threshold_minor=threshold,
                    anomaly_score=score,
                    reason=reason,
                    is_one_off=item.is_one_off,
                )
            )
        items.sort(
            key=lambda result: result.amount_minor - result.threshold_minor,
            reverse=True,
        )
        return AbnormalTransactionsOutput(
            items=items[: params.max_results],
            strategy=params.threshold_strategy,
            basis="An anomaly is unusual relative to history, not proof of waste or fraud.",
        )

    def calculate_saving_gap(self, params: SavingGapInput) -> SavingGapOutput:
        bounds = self._lookback_dates(params.lookback_months, params.as_of_month)
        if bounds is None:
            return SavingGapOutput(
                period_start_month=None,
                period_end_month=None,
                requested_months=params.lookback_months,
                months_with_data=0,
                average_income_minor=0,
                average_net_expense_minor=0,
                average_balance_minor=0,
                target_amount_minor=params.target_amount_minor,
                saving_gap_minor=params.target_amount_minor,
                data_sufficient=False,
                basis="No transaction data is available.",
            )
        _, _, start_month, end_month = bounds
        summary = self.get_monthly_summary(
            MonthlySummaryInput(
                start_month=start_month,
                end_month=end_month,
                exclude_one_off=params.exclude_one_off,
            )
        )
        return SavingGapOutput(
            period_start_month=start_month,
            period_end_month=end_month,
            requested_months=params.lookback_months,
            months_with_data=summary.months_with_data,
            average_income_minor=summary.average_income_minor,
            average_net_expense_minor=summary.average_net_expense_minor,
            average_balance_minor=summary.average_balance_minor,
            target_amount_minor=params.target_amount_minor,
            saving_gap_minor=max(params.target_amount_minor - summary.average_balance_minor, 0),
            data_sufficient=summary.months_with_data >= params.lookback_months,
            basis=summary.basis,
        )

    def simulate_budget(self, params: SimulateBudgetInput) -> SimulateBudgetOutput:
        target_start = _month_start(params.target_month)
        baseline_end = _shift_month(target_start, -1).strftime("%Y-%m")
        bounds = self._lookback_dates(params.lookback_months, baseline_end)
        if bounds is None:
            bounds = (
                _shift_month(target_start, -params.lookback_months),
                target_start - timedelta(days=1),
                _shift_month(target_start, -params.lookback_months).strftime("%Y-%m"),
                baseline_end,
            )
        start, end, start_month, end_month = bounds
        records = self._records_for_dates(start, end)
        records = self._filtered_financial_records(
            records,
            exclude_one_off=params.exclude_one_off_from_baseline,
        )
        months_with_data = len({item.occurred_at.strftime("%Y-%m") for item in records})
        divisor = max(months_with_data, 1)
        income_total = sum(
            item.amount_minor for item in records if item.transaction_type == "income"
        )
        category_totals = self._category_net(records)
        baseline_by_category = _allocate_average(category_totals, divisor)
        income = (
            params.income_assumption_minor
            if params.income_assumption_minor is not None
            else round(income_total / divisor)
        )
        warnings: list[str] = []
        if months_with_data < params.lookback_months:
            warnings.append(
                f"Only {months_with_data} of {params.lookback_months} requested months "
                "contain data."
            )

        categories = (
            set(baseline_by_category)
            | set(params.category_budgets_minor)
            | set(params.category_reductions_minor)
        )
        items: list[BudgetSimulationItem] = []
        for category in sorted(categories):
            baseline = baseline_by_category.get(category, 0)
            if category in params.category_budgets_minor:
                target = params.category_budgets_minor[category]
                if category not in baseline_by_category:
                    warnings.append(f"Category '{category}' has no baseline history.")
                if target > baseline:
                    warnings.append(f"Category '{category}' budget exceeds its baseline.")
            elif category in params.category_reductions_minor:
                requested = params.category_reductions_minor[category]
                if requested > baseline:
                    warnings.append(f"Category '{category}' reduction was capped at its baseline.")
                target = max(baseline - requested, 0)
            else:
                target = baseline
            items.append(
                BudgetSimulationItem(
                    category=category,
                    baseline_minor=baseline,
                    target_minor=target,
                    reduction_minor=max(baseline - target, 0),
                )
            )

        baseline_expense = sum(item.baseline_minor for item in items)
        projected_expense = sum(item.target_minor for item in items)
        planned_reduction = sum(item.reduction_minor for item in items)
        baseline_balance = income - baseline_expense
        projected_balance = income - projected_expense
        required_reduction = max(params.target_saving_minor - baseline_balance, 0)
        return SimulateBudgetOutput(
            target_month=params.target_month,
            baseline_period_start_month=start_month,
            baseline_period_end_month=end_month,
            months_with_data=months_with_data,
            baseline_income_minor=income,
            baseline_expense_minor=baseline_expense,
            baseline_balance_minor=baseline_balance,
            target_saving_minor=params.target_saving_minor,
            required_reduction_minor=required_reduction,
            planned_reduction_minor=planned_reduction,
            projected_expense_minor=projected_expense,
            projected_balance_minor=projected_balance,
            target_met=projected_balance >= params.target_saving_minor,
            items=items,
            warnings=warnings,
            basis=(
                "Projection uses average category net expenses from months with data; "
                f"exclude_one_off={params.exclude_one_off_from_baseline}."
            ),
        )
