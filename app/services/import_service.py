from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.importers.base import BaseBillAdapter
from app.models import ImportBatch, Transaction
from app.schemas import ImportReport, TransactionInput


def file_fingerprint(file_path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(file_path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(64 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def transaction_dedup_key(record: TransactionInput) -> str:
    if record.source_transaction_id:
        raw = f"source-id|{record.source}|{record.source_transaction_id}"
    else:
        canonical = {
            "source": record.source,
            "occurred_at": record.occurred_at.isoformat(),
            "transaction_type": record.transaction_type,
            "amount_minor": record.amount_minor,
            "merchant": record.merchant_normalized,
            "description": record.description_safe,
        }
        raw = "canonical|" + json.dumps(canonical, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def import_bill(
    session: Session,
    *,
    adapter: BaseBillAdapter,
    file_path: str | Path,
    user_id: str,
) -> ImportReport:
    path = Path(file_path)
    parsed = adapter.parse(path)
    fingerprint = file_fingerprint(path)
    existing_batch = session.scalar(
        select(ImportBatch).where(
            ImportBatch.user_id == user_id,
            ImportBatch.source == adapter.source,
            ImportBatch.file_fingerprint == fingerprint,
        )
    )
    if existing_batch:
        return ImportReport(
            import_batch_id=existing_batch.id,
            source=adapter.source,
            total_rows=len(parsed.records) + len(parsed.issues),
            imported_rows=0,
            duplicate_rows=len(parsed.records),
            ignored_rows=0,
            failed_rows=len(parsed.issues),
            already_imported_file=True,
        )

    batch = ImportBatch(
        id=str(uuid4()),
        user_id=user_id,
        source=adapter.source,
        original_filename=path.name,
        file_fingerprint=fingerprint,
        status="processing",
        total_rows=len(parsed.records) + len(parsed.issues),
        failed_rows=len(parsed.issues),
    )
    session.add(batch)
    session.flush()

    imported_rows = 0
    duplicate_rows = 0
    for record in parsed.records:
        dedup_key = transaction_dedup_key(record)
        exists = session.scalar(
            select(Transaction.id).where(
                Transaction.user_id == user_id,
                Transaction.dedup_key == dedup_key,
            )
        )
        if exists:
            duplicate_rows += 1
            continue
        session.add(
            Transaction(
                id=str(uuid4()),
                user_id=user_id,
                import_batch_id=batch.id,
                source=record.source,
                source_transaction_id=record.source_transaction_id,
                occurred_at=record.occurred_at,
                direction=record.direction,
                transaction_type=record.transaction_type,
                amount_minor=record.amount_minor,
                currency=record.currency,
                merchant_normalized=record.merchant_normalized,
                description_safe=record.description_safe,
                category=record.category,
                expense_nature=record.expense_nature,
                is_recurring=record.is_recurring,
                is_one_off=record.is_one_off,
                related_transaction_id=record.related_transaction_id,
                dedup_key=dedup_key,
            )
        )
        imported_rows += 1

    batch.imported_rows = imported_rows
    batch.duplicate_rows = duplicate_rows
    batch.ignored_rows = 0
    batch.status = "completed" if not parsed.issues else "partial"
    batch.completed_at = datetime.now(UTC)
    session.commit()
    return ImportReport(
        import_batch_id=batch.id,
        source=adapter.source,
        total_rows=batch.total_rows,
        imported_rows=imported_rows,
        duplicate_rows=duplicate_rows,
        ignored_rows=0,
        failed_rows=batch.failed_rows,
    )
