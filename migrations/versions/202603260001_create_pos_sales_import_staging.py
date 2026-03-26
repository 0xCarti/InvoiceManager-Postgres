"""Create POS sales import staging tables.

Revision ID: 202603260001
Revises: 202603210002
Create Date: 2026-03-26 00:00:00.000000

"""

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision = "202603260001"
down_revision = "202603210002"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "pos_sales_import",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("source_provider", sa.String(length=100), nullable=False),
        sa.Column("message_id", sa.String(length=255), nullable=False),
        sa.Column("attachment_filename", sa.String(length=255), nullable=False),
        sa.Column("attachment_sha256", sa.String(length=64), nullable=False),
        sa.Column("received_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("status", sa.String(length=32), server_default="pending", nullable=False),
        sa.Column("approved_by", sa.Integer(), nullable=True),
        sa.Column("approved_at", sa.DateTime(), nullable=True),
        sa.Column("reversed_by", sa.Integer(), nullable=True),
        sa.Column("reversed_at", sa.DateTime(), nullable=True),
        sa.Column("approval_batch_id", sa.String(length=64), nullable=True),
        sa.Column("reversal_batch_id", sa.String(length=64), nullable=True),
        sa.Column("failure_reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.CheckConstraint(
            "status IN ('pending', 'needs_mapping', 'approved', 'reversed', 'deleted', 'failed')",
            name="ck_pos_sales_import_status",
        ),
        sa.ForeignKeyConstraint(["approved_by"], ["user.id"]),
        sa.ForeignKeyConstraint(["reversed_by"], ["user.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "source_provider",
            "message_id",
            "attachment_sha256",
            name="uq_pos_sales_import_idempotency",
        ),
    )
    op.create_index(
        "ix_pos_sales_import_status_received_at",
        "pos_sales_import",
        ["status", "received_at"],
        unique=False,
    )
    op.create_index("ix_pos_sales_import_received_at", "pos_sales_import", ["received_at"], unique=False)
    op.create_index("ix_pos_sales_import_approved_by", "pos_sales_import", ["approved_by", "approved_at"], unique=False)
    op.create_index("ix_pos_sales_import_reversed_by", "pos_sales_import", ["reversed_by", "reversed_at"], unique=False)
    op.create_index("ix_pos_sales_import_approval_batch", "pos_sales_import", ["approval_batch_id"], unique=False)
    op.create_index("ix_pos_sales_import_reversal_batch", "pos_sales_import", ["reversal_batch_id"], unique=False)

    op.create_table(
        "pos_sales_import_location",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("import_id", sa.Integer(), nullable=False),
        sa.Column("source_location_name", sa.String(length=255), nullable=False),
        sa.Column("normalized_location_name", sa.String(length=255), nullable=False),
        sa.Column("location_id", sa.Integer(), nullable=True),
        sa.Column("total_quantity", sa.Float(), server_default="0.0", nullable=False),
        sa.Column("net_inc", sa.Float(), server_default="0.0", nullable=False),
        sa.Column("discounts_abs", sa.Float(), server_default="0.0", nullable=False),
        sa.Column("computed_total", sa.Float(), server_default="0.0", nullable=False),
        sa.Column("parse_index", sa.Integer(), nullable=False),
        sa.Column("approval_batch_id", sa.String(length=64), nullable=True),
        sa.Column("reversal_batch_id", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.ForeignKeyConstraint(["import_id"], ["pos_sales_import.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["location_id"], ["location.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("import_id", "parse_index", name="uq_pos_sales_import_location_order"),
    )
    op.create_index("ix_pos_sales_import_location_import", "pos_sales_import_location", ["import_id"], unique=False)
    op.create_index(
        "ix_pos_sales_import_location_normalized",
        "pos_sales_import_location",
        ["normalized_location_name"],
        unique=False,
    )
    op.create_index("ix_pos_sales_import_location_location_id", "pos_sales_import_location", ["location_id"], unique=False)
    op.create_index("ix_pos_sales_import_location_approval_batch", "pos_sales_import_location", ["approval_batch_id"], unique=False)
    op.create_index("ix_pos_sales_import_location_reversal_batch", "pos_sales_import_location", ["reversal_batch_id"], unique=False)

    op.create_table(
        "pos_sales_import_row",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("import_id", sa.Integer(), nullable=False),
        sa.Column("location_import_id", sa.Integer(), nullable=False),
        sa.Column("source_product_code", sa.String(length=128), nullable=True),
        sa.Column("source_product_name", sa.String(length=255), nullable=False),
        sa.Column("normalized_product_name", sa.String(length=255), nullable=False),
        sa.Column("product_id", sa.Integer(), nullable=True),
        sa.Column("quantity", sa.Float(), server_default="0.0", nullable=False),
        sa.Column("net_inc", sa.Float(), server_default="0.0", nullable=False),
        sa.Column("discount_raw", sa.String(length=64), nullable=True),
        sa.Column("discount_abs", sa.Float(), server_default="0.0", nullable=False),
        sa.Column("computed_line_total", sa.Float(), server_default="0.0", nullable=False),
        sa.Column("computed_unit_price", sa.Float(), server_default="0.0", nullable=False),
        sa.Column("parse_index", sa.Integer(), nullable=False),
        sa.Column("is_zero_quantity", sa.Boolean(), server_default="0", nullable=False),
        sa.Column("approval_batch_id", sa.String(length=64), nullable=True),
        sa.Column("reversal_batch_id", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.ForeignKeyConstraint(["import_id"], ["pos_sales_import.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["location_import_id"], ["pos_sales_import_location.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["product_id"], ["product.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("location_import_id", "parse_index", name="uq_pos_sales_import_row_order"),
    )
    op.create_index("ix_pos_sales_import_row_import", "pos_sales_import_row", ["import_id"], unique=False)
    op.create_index(
        "ix_pos_sales_import_row_location_import",
        "pos_sales_import_row",
        ["location_import_id"],
        unique=False,
    )
    op.create_index(
        "ix_pos_sales_import_row_normalized_product",
        "pos_sales_import_row",
        ["normalized_product_name"],
        unique=False,
    )
    op.create_index("ix_pos_sales_import_row_product_id", "pos_sales_import_row", ["product_id"], unique=False)
    op.create_index("ix_pos_sales_import_row_zero_qty", "pos_sales_import_row", ["is_zero_quantity"], unique=False)
    op.create_index("ix_pos_sales_import_row_approval_batch", "pos_sales_import_row", ["approval_batch_id"], unique=False)
    op.create_index("ix_pos_sales_import_row_reversal_batch", "pos_sales_import_row", ["reversal_batch_id"], unique=False)


def downgrade():
    op.drop_index("ix_pos_sales_import_row_reversal_batch", table_name="pos_sales_import_row")
    op.drop_index("ix_pos_sales_import_row_approval_batch", table_name="pos_sales_import_row")
    op.drop_index("ix_pos_sales_import_row_zero_qty", table_name="pos_sales_import_row")
    op.drop_index("ix_pos_sales_import_row_product_id", table_name="pos_sales_import_row")
    op.drop_index("ix_pos_sales_import_row_normalized_product", table_name="pos_sales_import_row")
    op.drop_index("ix_pos_sales_import_row_location_import", table_name="pos_sales_import_row")
    op.drop_index("ix_pos_sales_import_row_import", table_name="pos_sales_import_row")
    op.drop_table("pos_sales_import_row")

    op.drop_index("ix_pos_sales_import_location_reversal_batch", table_name="pos_sales_import_location")
    op.drop_index("ix_pos_sales_import_location_approval_batch", table_name="pos_sales_import_location")
    op.drop_index("ix_pos_sales_import_location_location_id", table_name="pos_sales_import_location")
    op.drop_index("ix_pos_sales_import_location_normalized", table_name="pos_sales_import_location")
    op.drop_index("ix_pos_sales_import_location_import", table_name="pos_sales_import_location")
    op.drop_table("pos_sales_import_location")

    op.drop_index("ix_pos_sales_import_reversal_batch", table_name="pos_sales_import")
    op.drop_index("ix_pos_sales_import_approval_batch", table_name="pos_sales_import")
    op.drop_index("ix_pos_sales_import_reversed_by", table_name="pos_sales_import")
    op.drop_index("ix_pos_sales_import_approved_by", table_name="pos_sales_import")
    op.drop_index("ix_pos_sales_import_received_at", table_name="pos_sales_import")
    op.drop_index("ix_pos_sales_import_status_received_at", table_name="pos_sales_import")
    op.drop_table("pos_sales_import")
