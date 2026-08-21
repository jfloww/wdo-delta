"""persist imported transactions

Revision ID: a6128d6e4f20
Revises: e438a28dd961
Create Date: 2026-08-21 10:15:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a6128d6e4f20"
down_revision: str | Sequence[str] | None = "e438a28dd961"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add multiplicity-aware transaction storage."""
    op.create_table(
        "transactions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("imported_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("account", sa.String(length=200), nullable=False),
        sa.Column("posted_on", sa.Date(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("normalised_merchant", sa.Text(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("amount", sa.Numeric(precision=18, scale=2), nullable=False),
        sa.Column("fingerprint", sa.String(length=32), nullable=False),
        sa.Column("occurrence", sa.Integer(), nullable=False),
        sa.Column("source_file", sa.String(length=255), nullable=False),
        sa.Column("source_line", sa.Integer(), nullable=False),
        sa.Column("raw_cells", sa.JSON(), nullable=False),
        sa.CheckConstraint(
            "occurrence > 0",
            name="ck_transactions_occurrence_positive",
        ),
        sa.CheckConstraint(
            "source_line > 1",
            name="ck_transactions_source_line_after_header",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "account",
            "fingerprint",
            "occurrence",
            name="uq_transactions_account_fingerprint_occurrence",
        ),
    )
    op.create_index(
        "ix_transactions_account_posted_on",
        "transactions",
        ["account", "posted_on"],
        unique=False,
    )
    op.create_index(
        "ix_transactions_fingerprint",
        "transactions",
        ["fingerprint"],
        unique=False,
    )
    op.create_index(
        "ix_transactions_imported_at",
        "transactions",
        ["imported_at"],
        unique=False,
    )


def downgrade() -> None:
    """Remove imported transaction storage."""
    op.drop_index("ix_transactions_imported_at", table_name="transactions")
    op.drop_index("ix_transactions_fingerprint", table_name="transactions")
    op.drop_index("ix_transactions_account_posted_on", table_name="transactions")
    op.drop_table("transactions")
