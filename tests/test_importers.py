from __future__ import annotations

from pathlib import Path

from app.demo.generator import generate_demo_dataset
from app.importers import AlipayAdapter, WechatAdapter


def test_both_demo_bill_formats_normalize_without_errors(tmp_path: Path) -> None:
    truth = generate_demo_dataset(tmp_path)
    wechat = WechatAdapter().parse(tmp_path / "demo_wechat_bill.csv")
    alipay = AlipayAdapter().parse(tmp_path / "demo_alipay_bill.csv")

    assert not wechat.issues
    assert not alipay.issues
    assert len(wechat.records) == truth["source_counts"]["wechat"]
    assert len(alipay.records) == truth["source_counts"]["alipay"]
    assert len(wechat.records) + len(alipay.records) == truth["transaction_count"]
    assert {record.source for record in wechat.records} == {"wechat"}
    assert {record.source for record in alipay.records} == {"alipay"}
    assert all(record.amount_minor >= 0 for record in wechat.records + alipay.records)
