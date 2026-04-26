"""add equipment registry

Revision ID: e7f8a9b0c1d2
Revises: d6e7f8a9b0c1
Create Date: 2026-04-25 09:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "e7f8a9b0c1d2"
down_revision = "d6e7f8a9b0c1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "equipment_category",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("archived", sa.Boolean(), nullable=False, server_default="0"),
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
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_equipment_category_archived",
        "equipment_category",
        ["archived"],
        unique=False,
    )
    op.create_index(
        "uix_equipment_category_name_active",
        "equipment_category",
        ["name"],
        unique=True,
        postgresql_where=sa.text("archived = false"),
    )

    op.create_table(
        "equipment_model",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("category_id", sa.Integer(), nullable=False),
        sa.Column("manufacturer", sa.String(length=120), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("model_number", sa.String(length=120), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("archived", sa.Boolean(), nullable=False, server_default="0"),
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
        sa.ForeignKeyConstraint(["category_id"], ["equipment_category.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_equipment_model_archived",
        "equipment_model",
        ["archived"],
        unique=False,
    )
    op.create_index(
        "ix_equipment_model_category_id",
        "equipment_model",
        ["category_id"],
        unique=False,
    )
    op.create_index(
        "uix_equipment_model_identity_active",
        "equipment_model",
        ["category_id", "manufacturer", "name", "model_number"],
        unique=True,
        postgresql_where=sa.text("archived = false"),
    )

    op.create_table(
        "equipment_asset",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("equipment_model_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=True),
        sa.Column("asset_tag", sa.String(length=64), nullable=False),
        sa.Column("serial_number", sa.String(length=128), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "status",
            sa.String(length=32),
            nullable=False,
            server_default="operational",
        ),
        sa.Column("acquired_on", sa.Date(), nullable=True),
        sa.Column("warranty_expires_on", sa.Date(), nullable=True),
        sa.Column("cost", sa.Float(), nullable=True),
        sa.Column("purchase_vendor_id", sa.Integer(), nullable=True),
        sa.Column("service_vendor_id", sa.Integer(), nullable=True),
        sa.Column("service_contact_name", sa.String(length=120), nullable=True),
        sa.Column("service_contact_email", sa.String(length=255), nullable=True),
        sa.Column("service_contact_phone", sa.String(length=50), nullable=True),
        sa.Column("location_id", sa.Integer(), nullable=True),
        sa.Column("sublocation", sa.String(length=120), nullable=True),
        sa.Column("assigned_user_id", sa.Integer(), nullable=True),
        sa.Column("archived", sa.Boolean(), nullable=False, server_default="0"),
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
            "status IN ('operational', 'needs_service', 'out_of_service', 'retired', 'disposed', 'lost')",
            name="ck_equipment_asset_status",
        ),
        sa.ForeignKeyConstraint(["assigned_user_id"], ["user.id"]),
        sa.ForeignKeyConstraint(["equipment_model_id"], ["equipment_model.id"]),
        sa.ForeignKeyConstraint(["location_id"], ["location.id"]),
        sa.ForeignKeyConstraint(["purchase_vendor_id"], ["vendor.id"]),
        sa.ForeignKeyConstraint(["service_vendor_id"], ["vendor.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_equipment_asset_archived",
        "equipment_asset",
        ["archived"],
        unique=False,
    )
    op.create_index(
        "ix_equipment_asset_status",
        "equipment_asset",
        ["status"],
        unique=False,
    )
    op.create_index(
        "ix_equipment_asset_model_id",
        "equipment_asset",
        ["equipment_model_id"],
        unique=False,
    )
    op.create_index(
        "ix_equipment_asset_purchase_vendor_id",
        "equipment_asset",
        ["purchase_vendor_id"],
        unique=False,
    )
    op.create_index(
        "ix_equipment_asset_service_vendor_id",
        "equipment_asset",
        ["service_vendor_id"],
        unique=False,
    )
    op.create_index(
        "ix_equipment_asset_location_id",
        "equipment_asset",
        ["location_id"],
        unique=False,
    )
    op.create_index(
        "ix_equipment_asset_assigned_user_id",
        "equipment_asset",
        ["assigned_user_id"],
        unique=False,
    )
    op.create_index(
        "uix_equipment_asset_tag",
        "equipment_asset",
        ["asset_tag"],
        unique=True,
    )
    op.create_index(
        "uix_equipment_asset_serial_number",
        "equipment_asset",
        ["serial_number"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("uix_equipment_asset_serial_number", table_name="equipment_asset")
    op.drop_index("uix_equipment_asset_tag", table_name="equipment_asset")
    op.drop_index("ix_equipment_asset_assigned_user_id", table_name="equipment_asset")
    op.drop_index("ix_equipment_asset_location_id", table_name="equipment_asset")
    op.drop_index("ix_equipment_asset_service_vendor_id", table_name="equipment_asset")
    op.drop_index("ix_equipment_asset_purchase_vendor_id", table_name="equipment_asset")
    op.drop_index("ix_equipment_asset_model_id", table_name="equipment_asset")
    op.drop_index("ix_equipment_asset_status", table_name="equipment_asset")
    op.drop_index("ix_equipment_asset_archived", table_name="equipment_asset")
    op.drop_table("equipment_asset")

    op.drop_index(
        "uix_equipment_model_identity_active",
        table_name="equipment_model",
    )
    op.drop_index("ix_equipment_model_category_id", table_name="equipment_model")
    op.drop_index("ix_equipment_model_archived", table_name="equipment_model")
    op.drop_table("equipment_model")

    op.drop_index(
        "uix_equipment_category_name_active",
        table_name="equipment_category",
    )
    op.drop_index(
        "ix_equipment_category_archived",
        table_name="equipment_category",
    )
    op.drop_table("equipment_category")
