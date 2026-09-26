from __future__ import annotations

import csv
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from io import StringIO
from pathlib import Path


def read_rows_from_header(
    file_path: str | Path, *, required_columns: tuple[str, ...]
) -> tuple[list[dict[str, str]], int]:
    raw = Path(file_path).read_bytes()
    text: str | None = None
    last_error: UnicodeDecodeError | None = None
    for encoding in ("utf-8-sig", "gb18030"):
        try:
            text = raw.decode(encoding)
            break
        except UnicodeDecodeError as exc:
            last_error = exc
    if text is None:
        raise ValueError("账单文件编码无法识别") from last_error

    lines = text.splitlines()
    header_index = next(
        (
            index
            for index, line in enumerate(lines)
            if all(column in line for column in required_columns)
        ),
        None,
    )
    if header_index is None:
        raise ValueError(f"找不到账单表头，必须包含：{', '.join(required_columns)}")
    reader = csv.DictReader(StringIO("\n".join(lines[header_index:])))
    rows = [
        {str(key).strip(): (value or "").strip() for key, value in row.items() if key is not None}
        for row in reader
        if any((value or "").strip() for value in row.values())
    ]
    return rows, header_index + 2


def parse_datetime(value: str) -> datetime:
    cleaned = value.strip()
    for pattern in ("%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(cleaned, pattern)
        except ValueError:
            continue
    raise ValueError(f"无法识别交易时间：{value}")


def parse_amount_minor(value: str) -> int:
    cleaned = value.strip().replace("¥", "").replace("￥", "").replace("元", "").replace(",", "")
    if not cleaned:
        raise ValueError("金额为空")
    amount = Decimal(cleaned).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return int(amount * 100)


def parse_bool(value: str) -> bool:
    return value.strip().lower() in {"是", "true", "1", "yes", "y"}


def normalize_nature(value: str, transaction_type: str) -> str:
    if transaction_type in {"income", "refund", "transfer"}:
        return "non_expense"
    aliases = {"固定": "fixed", "fixed": "fixed", "可变": "variable", "variable": "variable"}
    return aliases.get(value.strip().lower(), "variable")


def fallback_category(merchant: str, description: str, transaction_type: str) -> str:
    if transaction_type == "transfer":
        return "转账"
    if transaction_type == "income":
        return "工资" if "工资" in description else "其他收入"
    text = f"{merchant} {description}"
    rules = [
        (("饭", "餐", "咖啡", "外卖", "面馆"), "餐饮"),
        (("地铁", "公交", "网约车", "出行"), "交通"),
        (("会员", "订阅", "云服务"), "订阅"),
        (("公寓", "房租", "月租"), "住房"),
        (("超市", "电商", "便利店", "商城", "数码"), "购物"),
        (("影院", "桌游", "展览"), "娱乐"),
        (("水电", "燃气", "话费", "通信"), "生活缴费"),
    ]
    for keywords, category in rules:
        if any(keyword in text for keyword in keywords):
            return category
    return "其他"
