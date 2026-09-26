from __future__ import annotations

import hashlib
import json
from pathlib import Path

from app.demo.generator import generate_demo_dataset


def _hashes(directory: Path) -> dict[str, str]:
    return {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(directory.iterdir())
        if path.is_file()
    }


def test_demo_dataset_is_reproducible_and_has_required_shape(tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    first_truth = generate_demo_dataset(first)
    second_truth = generate_demo_dataset(second)

    assert _hashes(first) == _hashes(second)
    assert first_truth == second_truth
    assert 300 <= first_truth["transaction_count"] <= 500
    assert set(first_truth["source_counts"]) == {"wechat", "alipay"}
    assert len(first_truth["monthly_summary"]) == 6
    assert first_truth["transaction_type_counts"]["refund"] >= 1
    assert first_truth["transaction_type_counts"]["transfer"] >= 1
    assert len(first_truth["one_off_transaction_ids"]) == 1
    assert 0 < first_truth["saving_gap_minor"] < first_truth["saving_goal_minor"]

    persisted = json.loads((first / "demo_ground_truth.json").read_text(encoding="utf-8"))
    assert persisted == first_truth
