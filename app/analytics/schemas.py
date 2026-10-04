from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

TransactionType = Literal["income", "expense", "refund", "transfer"]


class ToolInputModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


def validate_month(value: str) -> str:
    try:
        year_text, month_text = value.split("-", maxsplit=1)
        date(int(year_text), int(month_text), 1)
    except (TypeError, ValueError) as exc:
        raise ValueError("month must use YYYY-MM") from exc
    if len(value) != 7 or value[4] != "-":
        raise ValueError("month must use YYYY-MM")
    return value


class DateRangeInput(ToolInputModel):
    start_date: date
    end_date: date

    @model_validator(mode="after")
    def validate_range(self) -> DateRangeInput:
        if self.start_date > self.end_date:
            raise ValueError("start_date must not be after end_date")
        return self


class SearchTransactionsInput(ToolInputModel):
    start_date: date | None = None
    end_date: date | None = None
    categories: list[str] = Field(default_factory=list)
    merchant_query: str | None = Field(default=None, min_length=1, max_length=100)
    transaction_types: list[TransactionType] = Field(default_factory=list)
    min_amount_minor: int | None = Field(default=None, ge=0)
    max_amount_minor: int | None = Field(default=None, ge=0)
    limit: int = Field(default=20, ge=1, le=100)

    @model_validator(mode="after")
    def validate_ranges(self) -> SearchTransactionsInput:
        if self.start_date and self.end_date and self.start_date > self.end_date:
            raise ValueError("start_date must not be after end_date")
        if (
            self.min_amount_minor is not None
            and self.max_amount_minor is not None
            and self.min_amount_minor > self.max_amount_minor
        ):
            raise ValueError("min_amount_minor must not exceed max_amount_minor")
        return self


class TransactionSummary(BaseModel):
    transaction_id: str
    occurred_at: datetime
    transaction_type: TransactionType
    amount_minor: int
    merchant: str
    category: str
    expense_nature: str
    is_recurring: bool
    is_one_off: bool


class SearchTransactionsOutput(BaseModel):
    matched_count: int
    returned_count: int
    total_amount_minor: int
    net_cashflow_minor: int
    truncated: bool
    transactions: list[TransactionSummary]
    basis: str


class MonthlySummaryInput(ToolInputModel):
    start_month: str
    end_month: str
    exclude_transfers: bool = True
    refund_mode: Literal["net"] = "net"
    exclude_one_off: bool = False

    _validate_start = field_validator("start_month")(validate_month)
    _validate_end = field_validator("end_month")(validate_month)

    @model_validator(mode="after")
    def validate_range(self) -> MonthlySummaryInput:
        if self.start_month > self.end_month:
            raise ValueError("start_month must not be after end_month")
        if not self.exclude_transfers:
            raise ValueError("M2 only supports exclude_transfers=true")
        return self


class MonthlyAmount(BaseModel):
    month: str
    income_minor: int
    gross_expense_minor: int
    refund_minor: int
    net_expense_minor: int
    balance_minor: int
    transfer_minor_excluded: int
    transaction_count: int


class MonthlySummaryOutput(BaseModel):
    months: list[MonthlyAmount]
    requested_months: int
    months_with_data: int
    average_income_minor: int
    average_net_expense_minor: int
    average_balance_minor: int
    basis: str


class PeriodDefinition(ToolInputModel):
    start_date: date
    end_date: date

    @model_validator(mode="after")
    def validate_range(self) -> PeriodDefinition:
        if self.start_date > self.end_date:
            raise ValueError("period start_date must not be after end_date")
        return self


class ComparePeriodsInput(ToolInputModel):
    period_a: PeriodDefinition
    period_b: PeriodDefinition
    categories: list[str] = Field(default_factory=list)
    exclude_one_off: bool = False


class PeriodTotals(BaseModel):
    income_minor: int
    gross_expense_minor: int
    refund_minor: int
    net_expense_minor: int
    balance_minor: int
    transaction_count: int


class CategoryChange(BaseModel):
    category: str
    period_a_minor: int
    period_b_minor: int
    change_minor: int


class ComparePeriodsOutput(BaseModel):
    period_a: PeriodTotals
    period_b: PeriodTotals
    net_expense_change_minor: int
    net_expense_change_percent: float | None
    category_changes: list[CategoryChange]
    basis: str


class CategoryBreakdownInput(DateRangeInput):
    top_n: int = Field(default=10, ge=1, le=50)
    include_one_off: bool = True


class CategoryAmount(BaseModel):
    category: str
    net_expense_minor: int
    gross_expense_minor: int
    refund_minor: int
    fixed_minor: int
    variable_minor: int
    recurring_minor: int
    one_off_minor: int
    expense_count: int
    share_percent: float


class CategoryBreakdownOutput(BaseModel):
    total_net_expense_minor: int
    categories: list[CategoryAmount]
    truncated: bool
    basis: str


class RecurringExpensesInput(ToolInputModel):
    lookback_months: int = Field(default=6, ge=2, le=24)
    as_of_month: str | None = None
    min_occurrences: int = Field(default=3, ge=2, le=24)
    amount_tolerance_percent: float = Field(default=10.0, ge=0, le=100)

    @field_validator("as_of_month")
    @classmethod
    def validate_optional_month(cls, value: str | None) -> str | None:
        return validate_month(value) if value is not None else value


class RecurringExpense(BaseModel):
    merchant: str
    category: str
    occurrences: int
    active_months: int
    typical_amount_minor: int
    latest_date: date
    estimated_period: str
    confidence: float
    cancellable_candidate: bool
    candidate_label: str


class RecurringExpensesOutput(BaseModel):
    period_start_month: str | None
    period_end_month: str | None
    items: list[RecurringExpense]
    basis: str


class AbnormalTransactionsInput(DateRangeInput):
    category: str | None = None
    threshold_strategy: Literal["iqr", "median_multiplier"] = "iqr"
    threshold_multiplier: float = Field(default=3.0, ge=1.0, le=20.0)
    max_results: int = Field(default=10, ge=1, le=50)


class AbnormalTransaction(BaseModel):
    transaction_id: str
    occurred_at: datetime
    merchant: str
    category: str
    amount_minor: int
    baseline_median_minor: int
    threshold_minor: int
    anomaly_score: float
    reason: str
    is_one_off: bool


class AbnormalTransactionsOutput(BaseModel):
    items: list[AbnormalTransaction]
    strategy: str
    basis: str


class SavingGapInput(ToolInputModel):
    target_amount_minor: int = Field(gt=0)
    lookback_months: int = Field(default=3, ge=1, le=24)
    as_of_month: str | None = None
    exclude_one_off: bool = False

    @field_validator("as_of_month")
    @classmethod
    def validate_optional_month(cls, value: str | None) -> str | None:
        return validate_month(value) if value is not None else value


class SavingGapOutput(BaseModel):
    period_start_month: str | None
    period_end_month: str | None
    requested_months: int
    months_with_data: int
    average_income_minor: int
    average_net_expense_minor: int
    average_balance_minor: int
    target_amount_minor: int
    saving_gap_minor: int
    data_sufficient: bool
    basis: str


class SimulateBudgetInput(ToolInputModel):
    target_month: str
    target_saving_minor: int = Field(gt=0)
    lookback_months: int = Field(default=3, ge=1, le=24)
    income_assumption_minor: int | None = Field(default=None, ge=0)
    category_budgets_minor: dict[str, int] = Field(default_factory=dict)
    category_reductions_minor: dict[str, int] = Field(default_factory=dict)
    exclude_one_off_from_baseline: bool = False

    _validate_target = field_validator("target_month")(validate_month)

    @model_validator(mode="after")
    def validate_plan(self) -> SimulateBudgetInput:
        overlapping = set(self.category_budgets_minor) & set(self.category_reductions_minor)
        if overlapping:
            names = ", ".join(sorted(overlapping))
            raise ValueError(f"categories cannot have both a budget and reduction: {names}")
        for mapping in (self.category_budgets_minor, self.category_reductions_minor):
            if any(value < 0 for value in mapping.values()):
                raise ValueError("budget and reduction amounts must be non-negative")
        return self


class BudgetSimulationItem(BaseModel):
    category: str
    baseline_minor: int
    target_minor: int
    reduction_minor: int


class SimulateBudgetOutput(BaseModel):
    target_month: str
    baseline_period_start_month: str | None
    baseline_period_end_month: str | None
    months_with_data: int
    baseline_income_minor: int
    baseline_expense_minor: int
    baseline_balance_minor: int
    target_saving_minor: int
    required_reduction_minor: int
    planned_reduction_minor: int
    projected_expense_minor: int
    projected_balance_minor: int
    target_met: bool
    items: list[BudgetSimulationItem]
    warnings: list[str]
    basis: str
