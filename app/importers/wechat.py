from __future__ import annotations

from pathlib import Path

from app.importers.base import BaseBillAdapter
from app.importers.common import (
    fallback_category,
    normalize_nature,
    parse_amount_minor,
    parse_bool,
    parse_datetime,
    read_rows_from_header,
)
from app.schemas import AdapterResult, ParseIssue, TransactionInput


class WechatAdapter(BaseBillAdapter):
    source = "wechat"

    def parse(self, file_path: str | Path) -> AdapterResult:
        rows, first_data_row = read_rows_from_header(
            file_path, required_columns=("交易时间", "交易单号")
        )
        records: list[TransactionInput] = []
        issues: list[ParseIssue] = []
        for offset, row in enumerate(rows):
            try:
                transaction_label = row.get("交易类型", "")
                direction_label = row.get("收/支", "")
                if "退款" in transaction_label or "退款" in row.get("当前状态", ""):
                    transaction_type = "refund"
                    direction = "income"
                elif "转账" in transaction_label or direction_label == "不计收支":
                    transaction_type = "transfer"
                    direction = "neutral"
                elif direction_label == "收入":
                    transaction_type = "income"
                    direction = "income"
                else:
                    transaction_type = "expense"
                    direction = "expense"
                merchant = row.get("交易对方", "") or "未知交易方"
                description = row.get("商品", "")
                category = row.get("交易分类", "") or fallback_category(
                    merchant, description, transaction_type
                )
                records.append(
                    TransactionInput(
                        source=self.source,
                        source_transaction_id=row.get("交易单号") or None,
                        occurred_at=parse_datetime(row.get("交易时间", "")),
                        direction=direction,
                        transaction_type=transaction_type,
                        amount_minor=parse_amount_minor(row.get("金额(元)", "")),
                        merchant_normalized=merchant,
                        description_safe=description,
                        category=category,
                        expense_nature=normalize_nature(row.get("支出性质", ""), transaction_type),
                        is_recurring=parse_bool(row.get("周期性", "")),
                        is_one_off=parse_bool(row.get("一次性", "")),
                        related_transaction_id=row.get("关联交易单号") or None,
                    )
                )
            except (TypeError, ValueError) as exc:
                issues.append(ParseIssue(row_number=first_data_row + offset, message=str(exc)))
        return AdapterResult(records=records, issues=issues)
