from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

SourceName = Literal["wechat", "alipay"]
Direction = Literal["income", "expense", "neutral"]
TransactionType = Literal["income", "expense", "refund", "transfer"]
ExpenseNature = Literal["fixed", "variable", "non_expense"]


class TransactionInput(BaseModel):
    source: SourceName
    source_transaction_id: str | None = None
    occurred_at: datetime
    direction: Direction
    transaction_type: TransactionType
    amount_minor: int = Field(ge=0)
    currency: str = "CNY"
    merchant_normalized: str = Field(min_length=1, max_length=255)
    description_safe: str = Field(default="", max_length=500)
    category: str = Field(min_length=1, max_length=64)
    expense_nature: ExpenseNature
    is_recurring: bool = False
    is_one_off: bool = False
    related_transaction_id: str | None = None

    @field_validator("currency")
    @classmethod
    def normalize_currency(cls, value: str) -> str:
        return value.upper()


class ParseIssue(BaseModel):
    row_number: int
    message: str


class AdapterResult(BaseModel):
    records: list[TransactionInput]
    issues: list[ParseIssue] = Field(default_factory=list)


class ImportReport(BaseModel):
    import_batch_id: str
    source: SourceName
    total_rows: int
    imported_rows: int
    duplicate_rows: int
    ignored_rows: int
    failed_rows: int
    already_imported_file: bool = False
