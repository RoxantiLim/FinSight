from __future__ import annotations

import calendar
import csv
import json
import random
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from datetime import date, datetime, time
from pathlib import Path

DEFAULT_SEED = 42
DEFAULT_ANCHOR_DATE = date(2026, 8, 31)


@dataclass
class DemoTransaction:
    internal_id: str
    source: str
    occurred_at: datetime
    direction: str
    transaction_type: str
    amount_minor: int
    merchant_normalized: str
    description_safe: str
    category: str
    expense_nature: str
    is_recurring: bool = False
    is_one_off: bool = False
    related_internal_id: str | None = None
    source_transaction_id: str = ""
    related_transaction_id: str | None = None


def _month_sequence(anchor_date: date, count: int = 6) -> list[tuple[int, int]]:
    end_index = anchor_date.year * 12 + anchor_date.month - 1
    result: list[tuple[int, int]] = []
    for offset in range(count - 1, -1, -1):
        index = end_index - offset
        result.append((index // 12, index % 12 + 1))
    return result


def _timestamp(year: int, month: int, day: int, hour: int, minute: int) -> datetime:
    safe_day = min(day, calendar.monthrange(year, month)[1])
    return datetime.combine(date(year, month, safe_day), time(hour, minute))


def generate_transactions(
    *, seed: int = DEFAULT_SEED, anchor_date: date = DEFAULT_ANCHOR_DATE
) -> list[DemoTransaction]:
    rng = random.Random(seed)
    records: list[DemoTransaction] = []
    shopping_by_month: dict[str, list[DemoTransaction]] = defaultdict(list)
    internal_counter = 0

    def add(
        *,
        source: str,
        occurred_at: datetime,
        direction: str,
        transaction_type: str,
        amount_minor: int,
        merchant: str,
        description: str,
        category: str,
        nature: str,
        recurring: bool = False,
        one_off: bool = False,
        related_internal_id: str | None = None,
    ) -> DemoTransaction:
        nonlocal internal_counter
        internal_counter += 1
        item = DemoTransaction(
            internal_id=f"txn-{internal_counter:04d}",
            source=source,
            occurred_at=occurred_at,
            direction=direction,
            transaction_type=transaction_type,
            amount_minor=amount_minor,
            merchant_normalized=merchant,
            description_safe=description,
            category=category,
            expense_nature=nature,
            is_recurring=recurring,
            is_one_off=one_off,
            related_internal_id=related_internal_id,
        )
        records.append(item)
        return item

    months = _month_sequence(anchor_date)
    food_merchants = ["美味饭堂", "轻食工坊", "咖啡小站", "邻里面馆", "鲜选外卖"]
    transit_merchants = ["城市地铁", "公交出行", "网约车平台"]
    shopping_merchants = ["生活超市", "电商平台", "社区便利店", "家居商城"]

    for month_index, (year, month) in enumerate(months):
        month_key = f"{year:04d}-{month:02d}"
        add(
            source="alipay",
            occurred_at=_timestamp(year, month, 10, 9, 0),
            direction="income",
            transaction_type="income",
            amount_minor=620_000,
            merchant="示例科技有限公司",
            description="月度工资",
            category="工资",
            nature="non_expense",
            recurring=True,
        )
        add(
            source="wechat",
            occurred_at=_timestamp(year, month, 1, 8, 30),
            direction="expense",
            transaction_type="expense",
            amount_minor=200_000,
            merchant="安心公寓",
            description="月租",
            category="住房",
            nature="fixed",
            recurring=True,
        )

        for day, amount, merchant, description in [
            (3, 2_500, "云音乐", "音乐会员"),
            (5, 3_000, "视频空间", "视频会员"),
            (8, 3_400, "效率工具", "云服务订阅"),
        ]:
            add(
                source="alipay" if day != 5 else "wechat",
                occurred_at=_timestamp(year, month, day, 7, day),
                direction="expense",
                transaction_type="expense",
                amount_minor=amount,
                merchant=merchant,
                description=description,
                category="订阅",
                nature="fixed",
                recurring=True,
            )

        for day, amount, merchant, description in [
            (6, rng.randint(6_000, 8_500), "城市能源", "水电燃气"),
            (18, 9_900, "通信服务", "手机话费"),
        ]:
            add(
                source=rng.choice(["wechat", "alipay"]),
                occurred_at=_timestamp(year, month, day, 10, rng.randrange(60)),
                direction="expense",
                transaction_type="expense",
                amount_minor=amount,
                merchant=merchant,
                description=description,
                category="生活缴费",
                nature="fixed",
                recurring=True,
            )

        for index in range(24):
            day = 2 + (index * 5 + month_index) % 27
            recent_multiplier = 1.18 if month_index >= 3 and index % 3 == 0 else 1.0
            amount = int(rng.randint(1_800, 5_800) * recent_multiplier)
            merchant = rng.choice(food_merchants)
            add(
                source=rng.choice(["wechat", "wechat", "alipay"]),
                occurred_at=_timestamp(year, month, day, 8 + index % 13, rng.randrange(60)),
                direction="expense",
                transaction_type="expense",
                amount_minor=amount,
                merchant=merchant,
                description="工作餐" if index % 4 else "外卖",
                category="餐饮",
                nature="variable",
            )

        for index in range(18):
            day = 2 + (index * 7 + month_index * 2) % 27
            use_ride_hailing = index % 6 == 0
            merchant = "网约车平台" if use_ride_hailing else rng.choice(transit_merchants[:2])
            base_amount = rng.randint(2_500, 6_000) if use_ride_hailing else rng.randint(300, 700)
            if month_index >= 3 and use_ride_hailing:
                base_amount += 1_500
            add(
                source=rng.choice(["wechat", "alipay"]),
                occurred_at=_timestamp(year, month, day, 7 + index % 12, rng.randrange(60)),
                direction="expense",
                transaction_type="expense",
                amount_minor=base_amount,
                merchant=merchant,
                description="通勤出行",
                category="交通",
                nature="variable",
            )

        for index in range(5):
            day = 4 + index * 5
            shopping = add(
                source=rng.choice(["wechat", "alipay"]),
                occurred_at=_timestamp(year, month, day, 12 + index, rng.randrange(60)),
                direction="expense",
                transaction_type="expense",
                amount_minor=rng.randint(3_000, 28_000),
                merchant=rng.choice(shopping_merchants),
                description="日常购物",
                category="购物",
                nature="variable",
            )
            shopping_by_month[month_key].append(shopping)

        for index in range(3):
            add(
                source=rng.choice(["wechat", "alipay"]),
                occurred_at=_timestamp(year, month, 7 + index * 8, 19, rng.randrange(60)),
                direction="expense",
                transaction_type="expense",
                amount_minor=rng.randint(4_000, 16_000),
                merchant=rng.choice(["城市影院", "桌游空间", "周末展览"]),
                description="休闲娱乐",
                category="娱乐",
                nature="variable",
            )

        for index in range(2):
            add(
                source="wechat" if index == 0 else "alipay",
                occurred_at=_timestamp(year, month, 14 + index * 9, 16, month_index + index),
                direction="neutral",
                transaction_type="transfer",
                amount_minor=50_000 + index * 20_000,
                merchant="个人资金往来",
                description="账户间转账",
                category="转账",
                nature="non_expense",
            )

    refund_months = [
        f"{months[2][0]:04d}-{months[2][1]:02d}",
        f"{months[4][0]:04d}-{months[4][1]:02d}",
    ]
    for month_key in refund_months:
        original = shopping_by_month[month_key][1]
        add(
            source=original.source,
            occurred_at=original.occurred_at.replace(day=min(original.occurred_at.day + 2, 28)),
            direction="income",
            transaction_type="refund",
            amount_minor=original.amount_minor,
            merchant=original.merchant_normalized,
            description="购物退款",
            category=original.category,
            nature="non_expense",
            related_internal_id=original.internal_id,
        )

    anomaly = add(
        source="alipay",
        occurred_at=_timestamp(months[-1][0], months[-1][1], 22, 20, 15),
        direction="expense",
        transaction_type="expense",
        amount_minor=150_000,
        merchant="数码体验店",
        description="一次性电子产品",
        category="购物",
        nature="variable",
        one_off=True,
    )

    records.sort(key=lambda item: (item.occurred_at, item.internal_id))
    source_counters: Counter[str] = Counter()
    id_map: dict[str, str] = {}
    for item in records:
        source_counters[item.source] += 1
        prefix = "WX" if item.source == "wechat" else "ALI"
        item.source_transaction_id = (
            f"{prefix}{item.occurred_at:%Y%m%d}{source_counters[item.source]:06d}"
        )
        id_map[item.internal_id] = item.source_transaction_id
    for item in records:
        if item.related_internal_id:
            item.related_transaction_id = id_map[item.related_internal_id]

    assert anomaly.is_one_off
    return records


def _amount_text(amount_minor: int) -> str:
    return f"{amount_minor / 100:.2f}"


def _write_unified(path: Path, records: list[DemoTransaction]) -> None:
    fieldnames = [
        "source",
        "source_transaction_id",
        "occurred_at",
        "direction",
        "transaction_type",
        "amount_minor",
        "currency",
        "merchant_normalized",
        "description_safe",
        "category",
        "expense_nature",
        "is_recurring",
        "is_one_off",
        "related_transaction_id",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        for item in records:
            row = asdict(item)
            row["occurred_at"] = item.occurred_at.isoformat(sep=" ")
            row["currency"] = "CNY"
            writer.writerow({key: row.get(key, "") for key in fieldnames})


def _write_wechat(path: Path, records: list[DemoTransaction]) -> None:
    headers = [
        "交易时间",
        "交易类型",
        "交易对方",
        "商品",
        "收/支",
        "金额(元)",
        "支付方式",
        "当前状态",
        "交易单号",
        "商户单号",
        "交易分类",
        "支出性质",
        "周期性",
        "一次性",
        "关联交易单号",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        handle.write("微信支付账单明细,,,,,,,,,,,,,,\n")
        handle.write("微信昵称：[演示用户],,,,,,,,,,,,,,\n")
        handle.write("说明：该文件为 FinSight 生成的代表性演示账单,,,,,,,,,,,,,,\n")
        writer = csv.DictWriter(handle, fieldnames=headers, lineterminator="\n")
        writer.writeheader()
        for item in records:
            transaction_label = {
                "income": "工资收入",
                "expense": "商户消费",
                "refund": "退款",
                "transfer": "转账",
            }[item.transaction_type]
            direction_label = {
                "income": "收入",
                "expense": "支出",
                "neutral": "不计收支",
            }[item.direction]
            writer.writerow(
                {
                    "交易时间": item.occurred_at.strftime("%Y-%m-%d %H:%M:%S"),
                    "交易类型": transaction_label,
                    "交易对方": item.merchant_normalized,
                    "商品": item.description_safe,
                    "收/支": direction_label,
                    "金额(元)": f"¥{_amount_text(item.amount_minor)}",
                    "支付方式": "零钱",
                    "当前状态": "已退款" if item.transaction_type == "refund" else "支付成功",
                    "交易单号": item.source_transaction_id,
                    "商户单号": f"M-{item.source_transaction_id}",
                    "交易分类": item.category,
                    "支出性质": item.expense_nature,
                    "周期性": "是" if item.is_recurring else "否",
                    "一次性": "是" if item.is_one_off else "否",
                    "关联交易单号": item.related_transaction_id or "",
                }
            )


def _write_alipay(path: Path, records: list[DemoTransaction]) -> None:
    headers = [
        "交易时间",
        "交易分类",
        "交易对方",
        "商品说明",
        "收/支",
        "金额",
        "收/付款方式",
        "交易状态",
        "交易订单号",
        "商家订单号",
        "业务类型",
        "支出性质",
        "周期性",
        "一次性",
        "关联交易订单号",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        handle.write("支付宝交易记录明细查询,,,,,,,,,,,,,,\n")
        handle.write("账号：[演示账户],,,,,,,,,,,,,,\n")
        handle.write("说明：该文件为 FinSight 生成的代表性演示账单,,,,,,,,,,,,,,\n")
        writer = csv.DictWriter(handle, fieldnames=headers, lineterminator="\n")
        writer.writeheader()
        for item in records:
            direction_label = {
                "income": "收入",
                "expense": "支出",
                "neutral": "不计收支",
            }[item.direction]
            writer.writerow(
                {
                    "交易时间": item.occurred_at.strftime("%Y-%m-%d %H:%M:%S"),
                    "交易分类": item.category,
                    "交易对方": item.merchant_normalized,
                    "商品说明": item.description_safe,
                    "收/支": direction_label,
                    "金额": _amount_text(item.amount_minor),
                    "收/付款方式": "余额宝",
                    "交易状态": "退款成功" if item.transaction_type == "refund" else "交易成功",
                    "交易订单号": item.source_transaction_id,
                    "商家订单号": f"M-{item.source_transaction_id}",
                    "业务类型": item.transaction_type,
                    "支出性质": item.expense_nature,
                    "周期性": "是" if item.is_recurring else "否",
                    "一次性": "是" if item.is_one_off else "否",
                    "关联交易订单号": item.related_transaction_id or "",
                }
            )


def build_ground_truth(
    records: list[DemoTransaction], *, seed: int, anchor_date: date
) -> dict[str, object]:
    monthly: dict[str, dict[str, int]] = {}
    monthly_categories: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    category_totals: dict[str, int] = defaultdict(int)
    for item in records:
        month_key = item.occurred_at.strftime("%Y-%m")
        bucket = monthly.setdefault(
            month_key,
            {
                "income_minor": 0,
                "gross_expense_minor": 0,
                "refund_minor": 0,
                "net_expense_minor": 0,
                "balance_minor": 0,
                "transfer_minor_excluded": 0,
            },
        )
        if item.transaction_type == "income":
            bucket["income_minor"] += item.amount_minor
        elif item.transaction_type == "expense":
            bucket["gross_expense_minor"] += item.amount_minor
            monthly_categories[month_key][item.category] += item.amount_minor
            category_totals[item.category] += item.amount_minor
        elif item.transaction_type == "refund":
            bucket["refund_minor"] += item.amount_minor
            monthly_categories[month_key][item.category] -= item.amount_minor
            category_totals[item.category] -= item.amount_minor
        elif item.transaction_type == "transfer":
            bucket["transfer_minor_excluded"] += item.amount_minor

    for bucket in monthly.values():
        bucket["net_expense_minor"] = bucket["gross_expense_minor"] - bucket["refund_minor"]
        bucket["balance_minor"] = bucket["income_minor"] - bucket["net_expense_minor"]

    recent_months = sorted(monthly)[-3:]
    average_balance = round(sum(monthly[key]["balance_minor"] for key in recent_months) / 3)
    goal_minor = 200_000
    counts = Counter(item.transaction_type for item in records)
    sources = Counter(item.source for item in records)
    recent_categories: dict[str, int] = defaultdict(int)
    for month_key in recent_months:
        for category, amount in monthly_categories[month_key].items():
            recent_categories[category] += amount
    previous_month, latest_month = sorted(monthly)[-2:]
    comparison_categories = {
        category: monthly_categories[latest_month].get(category, 0)
        - monthly_categories[previous_month].get(category, 0)
        for category in set(monthly_categories[previous_month])
        | set(monthly_categories[latest_month])
    }
    return {
        "dataset_version": "m2-v1",
        "seed": seed,
        "anchor_date": anchor_date.isoformat(),
        "period": {"start_month": min(monthly), "end_month": max(monthly)},
        "transaction_count": len(records),
        "source_counts": dict(sorted(sources.items())),
        "transaction_type_counts": dict(sorted(counts.items())),
        "monthly_summary": dict(sorted(monthly.items())),
        "recent_three_months": recent_months,
        "recent_three_month_average_balance_minor": average_balance,
        "category_net_expense_minor": dict(sorted(category_totals.items())),
        "recent_three_month_category_net_expense_minor": dict(sorted(recent_categories.items())),
        "saving_goal_minor": goal_minor,
        "saving_gap_minor": max(0, goal_minor - average_balance),
        "refunds_reduce_expense": True,
        "transfers_excluded_from_income_and_expense": True,
        "one_off_transaction_ids": [
            item.source_transaction_id for item in records if item.is_one_off
        ],
        "known_recurring_merchants": sorted(
            {
                item.merchant_normalized
                for item in records
                if item.is_recurring and item.transaction_type == "expense"
            }
        ),
        "latest_month_comparison": {
            "period_a": previous_month,
            "period_b": latest_month,
            "net_expense_change_minor": (
                monthly[latest_month]["net_expense_minor"]
                - monthly[previous_month]["net_expense_minor"]
            ),
            "category_changes_minor": dict(sorted(comparison_categories.items())),
        },
        "recurring_transaction_count": sum(item.is_recurring for item in records),
    }


def generate_demo_dataset(
    output_dir: str | Path,
    *,
    seed: int = DEFAULT_SEED,
    anchor_date: date = DEFAULT_ANCHOR_DATE,
) -> dict[str, object]:
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    records = generate_transactions(seed=seed, anchor_date=anchor_date)
    _write_unified(output_path / "demo_transactions.csv", records)
    _write_wechat(
        output_path / "demo_wechat_bill.csv",
        [item for item in records if item.source == "wechat"],
    )
    _write_alipay(
        output_path / "demo_alipay_bill.csv",
        [item for item in records if item.source == "alipay"],
    )
    ground_truth = build_ground_truth(records, seed=seed, anchor_date=anchor_date)
    (output_path / "demo_ground_truth.json").write_text(
        json.dumps(ground_truth, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return ground_truth
