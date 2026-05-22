"""add public transfer requests

Revision ID: fe8f9a0b1c2
Revises: fd7e8f9a0b1
Create Date: 2026-05-22 13:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "fe8f9a0b1c2"
down_revision = "fd7e8f9a0b1"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "transfer_request",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("to_location_id", sa.Integer(), nullable=False),
        sa.Column("requested_by_name", sa.String(length=120), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "status",
            sa.String(length=16),
            server_default="pending",
            nullable=False,
        ),
        sa.Column("review_note", sa.Text(), nullable=True),
        sa.Column("reviewed_by", sa.Integer(), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(), nullable=True),
        sa.Column("converted_transfer_id", sa.Integer(), nullable=True),
        sa.Column(
            "submitted_at",
            sa.DateTime(),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'rejected', 'converted')",
            name="ck_transfer_request_status",
        ),
        sa.ForeignKeyConstraint(["converted_transfer_id"], ["transfer.id"]),
        sa.ForeignKeyConstraint(["reviewed_by"], ["user.id"]),
        sa.ForeignKeyConstraint(["to_location_id"], ["location.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_transfer_request_status_submitted",
        "transfer_request",
        ["status", "submitted_at"],
    )
    op.create_index(
        "ix_transfer_request_to_location",
        "transfer_request",
        ["to_location_id"],
    )
    op.create_index(
        "ix_transfer_request_converted_transfer",
        "transfer_request",
        ["converted_transfer_id"],
    )

    op.create_table(
        "transfer_request_item",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("transfer_request_id", sa.Integer(), nullable=False),
        sa.Column("item_id", sa.Integer(), nullable=False),
        sa.Column("quantity", sa.Float(), nullable=False),
        sa.Column("unit_id", sa.Integer(), nullable=True),
        sa.Column("unit_quantity", sa.Float(), nullable=True),
        sa.Column("base_quantity", sa.Float(), nullable=True),
        sa.Column("item_name", sa.String(length=100), server_default="", nullable=False),
        sa.ForeignKeyConstraint(["item_id"], ["item.id"]),
        sa.ForeignKeyConstraint(
            ["transfer_request_id"],
            ["transfer_request.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["unit_id"], ["item_unit.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_transfer_request_item_request",
        "transfer_request_item",
        ["transfer_request_id"],
    )
    op.create_index(
        "ix_transfer_request_item_item",
        "transfer_request_item",
        ["item_id"],
    )


def downgrade():
    op.drop_index(
        "ix_transfer_request_item_item",
        table_name="transfer_request_item",
    )
    op.drop_index(
        "ix_transfer_request_item_request",
        table_name="transfer_request_item",
    )
    op.drop_table("transfer_request_item")

    op.drop_index(
        "ix_transfer_request_converted_transfer",
        table_name="transfer_request",
    )
    op.drop_index(
        "ix_transfer_request_to_location",
        table_name="transfer_request",
    )
    op.drop_index(
        "ix_transfer_request_status_submitted",
        table_name="transfer_request",
    )
    op.drop_table("transfer_request")
