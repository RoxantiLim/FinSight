from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


def utc_now() -> datetime:
    return datetime.now(UTC)


class ImportBatch(Base):
    __tablename__ = "import_batches"
    __table_args__ = (
        UniqueConstraint(
            "user_id", "source", "file_fingerprint", name="uq_import_batch_fingerprint"
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    source: Mapped[str] = mapped_column(String(32), index=True)
    original_filename: Mapped[str] = mapped_column(String(255))
    file_fingerprint: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32), default="processing")
    total_rows: Mapped[int] = mapped_column(Integer, default=0)
    imported_rows: Mapped[int] = mapped_column(Integer, default=0)
    duplicate_rows: Mapped[int] = mapped_column(Integer, default=0)
    ignored_rows: Mapped[int] = mapped_column(Integer, default=0)
    failed_rows: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    transactions: Mapped[list[Transaction]] = relationship(back_populates="import_batch")


class Transaction(Base):
    __tablename__ = "transactions"
    __table_args__ = (
        CheckConstraint("amount_minor >= 0", name="ck_transaction_amount_non_negative"),
        UniqueConstraint(
            "user_id",
            "source",
            "source_transaction_id",
            name="uq_transaction_source_id",
        ),
        UniqueConstraint("user_id", "dedup_key", name="uq_transaction_dedup_key"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    import_batch_id: Mapped[str] = mapped_column(ForeignKey("import_batches.id"), index=True)
    source: Mapped[str] = mapped_column(String(32), index=True)
    source_transaction_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    direction: Mapped[str] = mapped_column(String(16))
    transaction_type: Mapped[str] = mapped_column(String(16), index=True)
    amount_minor: Mapped[int] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String(3), default="CNY")
    merchant_normalized: Mapped[str] = mapped_column(String(255))
    description_safe: Mapped[str] = mapped_column(String(500), default="")
    category: Mapped[str] = mapped_column(String(64), index=True)
    expense_nature: Mapped[str] = mapped_column(String(32))
    is_recurring: Mapped[bool] = mapped_column(Boolean, default=False)
    is_one_off: Mapped[bool] = mapped_column(Boolean, default=False)
    related_transaction_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    dedup_key: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    import_batch: Mapped[ImportBatch] = relationship(back_populates="transactions")
