from __future__ import annotations

from pathlib import Path

from sqlalchemy import func, inspect, select

from app.db import create_db_engine, create_schema, create_session_factory
from app.demo.generator import generate_demo_dataset
from app.importers import AlipayAdapter, WechatAdapter
from app.models import Transaction
from app.services.import_service import import_bill


def test_import_is_idempotent_and_financial_semantics_match_truth(tmp_path: Path) -> None:
    demo_dir = tmp_path / "demo"
    truth = generate_demo_dataset(demo_dir)
    engine = create_db_engine(tmp_path / "finsight.db")
    create_schema(engine)
    session_factory = create_session_factory(engine)
    unique_constraints = {
        tuple(item["column_names"])
        for item in inspect(engine).get_unique_constraints("transactions")
    }
    check_constraints = {
        item["name"] for item in inspect(engine).get_check_constraints("transactions")
    }

    assert ("user_id", "dedup_key") in unique_constraints
    assert ("user_id", "source", "source_transaction_id") in unique_constraints
    assert "ck_transaction_amount_non_negative" in check_constraints

    inputs = [
        (WechatAdapter(), demo_dir / "demo_wechat_bill.csv"),
        (AlipayAdapter(), demo_dir / "demo_alipay_bill.csv"),
    ]
    first_reports = []
    second_reports = []
    with session_factory() as session:
        for adapter, path in inputs:
            first_reports.append(
                import_bill(session, adapter=adapter, file_path=path, user_id="demo-user")
            )
        for adapter, path in inputs:
            second_reports.append(
                import_bill(session, adapter=adapter, file_path=path, user_id="demo-user")
            )

        count = session.scalar(select(func.count(Transaction.id)))
        income = session.scalar(
            select(func.coalesce(func.sum(Transaction.amount_minor), 0)).where(
                Transaction.transaction_type == "income"
            )
        )
        gross_expense = session.scalar(
            select(func.coalesce(func.sum(Transaction.amount_minor), 0)).where(
                Transaction.transaction_type == "expense"
            )
        )
        refunds = session.scalar(
            select(func.coalesce(func.sum(Transaction.amount_minor), 0)).where(
                Transaction.transaction_type == "refund"
            )
        )
        transfers = session.scalar(
            select(func.coalesce(func.sum(Transaction.amount_minor), 0)).where(
                Transaction.transaction_type == "transfer"
            )
        )

    assert sum(report.imported_rows for report in first_reports) == truth["transaction_count"]
    assert all(report.failed_rows == 0 for report in first_reports)
    assert all(report.imported_rows == 0 for report in second_reports)
    assert sum(report.duplicate_rows for report in second_reports) == truth["transaction_count"]
    assert all(report.already_imported_file for report in second_reports)
    assert count == truth["transaction_count"]

    monthly = truth["monthly_summary"].values()
    assert income == sum(item["income_minor"] for item in monthly)
    assert gross_expense == sum(item["gross_expense_minor"] for item in monthly)
    assert refunds == sum(item["refund_minor"] for item in monthly)
    assert transfers == sum(item["transfer_minor_excluded"] for item in monthly)
    assert gross_expense - refunds == sum(item["net_expense_minor"] for item in monthly)
