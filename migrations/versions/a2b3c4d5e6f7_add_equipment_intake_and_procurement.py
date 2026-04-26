"""add equipment intake and procurement

Revision ID: a2b3c4d5e6f7
Revises: f0a1b2c3d4e5
Create Date: 2026-04-26 10:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "a2b3c4d5e6f7"
down_revision = "f0a1b2c3d4e5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "equipment_intake_batch",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("equipment_model_id", sa.Integer(), nullable=False),
        sa.Column("purchase_vendor_id", sa.Integer(), nullable=True),
        sa.Column("vendor_name", sa.String(length=160), nullable=True),
        sa.Column("purchase_order_id", sa.Integer(), nullable=True),
        sa.Column("purchase_invoice_id", sa.Integer(), nullable=True),
        sa.Column("purchase_order_reference", sa.String(length=100), nullable=True),
        sa.Column("purchase_invoice_reference", sa.String(length=100), nullable=True),
        sa.Column(
            "source_type",
            sa.String(length=32),
            nullable=False,
            server_default="manual",
        ),
        sa.Column(
            "status",
            sa.String(length=20),
            nullable=False,
            server_default="open",
        ),
        sa.Column(
            "expected_quantity",
            sa.Integer(),
            nullable=False,
            server_default="1",
        ),
        sa.Column("unit_cost", sa.Float(), nullable=True),
        sa.Column("order_date", sa.Date(), nullable=True),
        sa.Column("expected_received_on", sa.Date(), nullable=True),
        sa.Column("received_on", sa.Date(), nullable=True),
        sa.Column("location_id", sa.Integer(), nullable=True),
        sa.Column("assigned_user_id", sa.Integer(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_by_id", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "source_type IN ('manual', 'purchase_order', 'purchase_invoice', 'snipe_it')",
            name="ck_equipment_intake_batch_source_type",
        ),
        sa.CheckConstraint(
            "status IN ('open', 'partial', 'received', 'cancelled')",
            name="ck_equipment_intake_batch_status",
        ),
        sa.CheckConstraint(
            "expected_quantity >= 1",
            name="ck_equipment_intake_batch_expected_quantity",
        ),
        sa.ForeignKeyConstraint(["equipment_model_id"], ["equipment_model.id"]),
        sa.ForeignKeyConstraint(["purchase_vendor_id"], ["vendor.id"]),
        sa.ForeignKeyConstraint(["purchase_order_id"], ["purchase_order.id"]),
        sa.ForeignKeyConstraint(["purchase_invoice_id"], ["purchase_invoice.id"]),
        sa.ForeignKeyConstraint(["location_id"], ["location.id"]),
        sa.ForeignKeyConstraint(["assigned_user_id"], ["user.id"]),
        sa.ForeignKeyConstraint(["created_by_id"], ["user.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_equipment_intake_batch_model_id",
        "equipment_intake_batch",
        ["equipment_model_id"],
        unique=False,
    )
    op.create_index(
        "ix_equipment_intake_batch_vendor_id",
        "equipment_intake_batch",
        ["purchase_vendor_id"],
        unique=False,
    )
    op.create_index(
        "ix_equipment_intake_batch_order_id",
        "equipment_intake_batch",
        ["purchase_order_id"],
        unique=False,
    )
    op.create_index(
        "ix_equipment_intake_batch_invoice_id",
        "equipment_intake_batch",
        ["purchase_invoice_id"],
        unique=False,
    )
    op.create_index(
        "ix_equipment_intake_batch_status",
        "equipment_intake_batch",
        ["status"],
        unique=False,
    )
    op.create_index(
        "ix_equipment_intake_batch_source_type",
        "equipment_intake_batch",
        ["source_type"],
        unique=False,
    )
    op.create_index(
        "ix_equipment_intake_batch_order_date",
        "equipment_intake_batch",
        ["order_date"],
        unique=False,
    )
    op.create_index(
        "ix_equipment_intake_batch_received_on",
        "equipment_intake_batch",
        ["received_on"],
        unique=False,
    )
    op.create_index(
        "ix_equipment_intake_batch_location_id",
        "equipment_intake_batch",
        ["location_id"],
        unique=False,
    )
    op.create_index(
        "ix_equipment_intake_batch_assigned_user_id",
        "equipment_intake_batch",
        ["assigned_user_id"],
        unique=False,
    )
    op.create_index(
        "ix_equipment_intake_batch_created_by_id",
        "equipment_intake_batch",
        ["created_by_id"],
        unique=False,
    )

    with op.batch_alter_table("equipment_asset") as batch_op:
        batch_op.add_column(
            sa.Column("equipment_intake_batch_id", sa.Integer(), nullable=True)
        )
        batch_op.create_index(
            "ix_equipment_asset_intake_batch_id",
            ["equipment_intake_batch_id"],
            unique=False,
        )
        batch_op.create_foreign_key(
            "fk_equipment_asset_intake_batch_id",
            "equipment_intake_batch",
            ["equipment_intake_batch_id"],
            ["id"],
        )


def downgrade() -> None:
    with op.batch_alter_table("equipment_asset") as batch_op:
        batch_op.drop_constraint("fk_equipment_asset_intake_batch_id", type_="foreignkey")
        batch_op.drop_index("ix_equipment_asset_intake_batch_id")
        batch_op.drop_column("equipment_intake_batch_id")

    op.drop_index(
        "ix_equipment_intake_batch_created_by_id",
        table_name="equipment_intake_batch",
    )
    op.drop_index(
        "ix_equipment_intake_batch_assigned_user_id",
        table_name="equipment_intake_batch",
    )
    op.drop_index(
        "ix_equipment_intake_batch_location_id",
        table_name="equipment_intake_batch",
    )
    op.drop_index(
        "ix_equipment_intake_batch_received_on",
        table_name="equipment_intake_batch",
    )
    op.drop_index(
        "ix_equipment_intake_batch_order_date",
        table_name="equipment_intake_batch",
    )
    op.drop_index(
        "ix_equipment_intake_batch_source_type",
        table_name="equipment_intake_batch",
    )
    op.drop_index(
        "ix_equipment_intake_batch_status",
        table_name="equipment_intake_batch",
    )
    op.drop_index(
        "ix_equipment_intake_batch_invoice_id",
        table_name="equipment_intake_batch",
    )
    op.drop_index(
        "ix_equipment_intake_batch_order_id",
        table_name="equipment_intake_batch",
    )
    op.drop_index(
        "ix_equipment_intake_batch_vendor_id",
        table_name="equipment_intake_batch",
    )
    op.drop_index(
        "ix_equipment_intake_batch_model_id",
        table_name="equipment_intake_batch",
    )
    op.drop_table("equipment_intake_batch")
