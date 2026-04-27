"""add equipment custody workflow

Revision ID: b4c5d6e7f8a9
Revises: a2b3c4d5e6f7
Create Date: 2026-04-26 15:30:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "b4c5d6e7f8a9"
down_revision = "a2b3c4d5e6f7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("equipment_asset") as batch_op:
        batch_op.add_column(sa.Column("home_location_id", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("checked_out_at", sa.DateTime(), nullable=True))
        batch_op.add_column(
            sa.Column(
                "label_qr_target",
                sa.String(length=20),
                nullable=False,
                server_default="detail",
            )
        )
        batch_op.add_column(
            sa.Column("label_qr_custom_url", sa.String(length=500), nullable=True)
        )
        batch_op.create_foreign_key(
            "fk_equipment_asset_home_location_id",
            "location",
            ["home_location_id"],
            ["id"],
        )
        batch_op.create_check_constraint(
            "ck_equipment_asset_label_qr_target",
            "label_qr_target IN ('detail', 'scan', 'custom')",
        )
        batch_op.create_index(
            "ix_equipment_asset_home_location_id",
            ["home_location_id"],
            unique=False,
        )
        batch_op.create_index(
            "ix_equipment_asset_checked_out_at",
            ["checked_out_at"],
            unique=False,
        )

    op.execute(
        "UPDATE equipment_asset "
        "SET home_location_id = location_id "
        "WHERE home_location_id IS NULL AND location_id IS NOT NULL"
    )

    op.create_table(
        "equipment_custody_event",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("equipment_asset_id", sa.Integer(), nullable=False),
        sa.Column(
            "action",
            sa.String(length=20),
            nullable=False,
            server_default="check_out",
        ),
        sa.Column("performed_by_id", sa.Integer(), nullable=True),
        sa.Column("from_location_id", sa.Integer(), nullable=True),
        sa.Column("to_location_id", sa.Integer(), nullable=True),
        sa.Column("from_assigned_user_id", sa.Integer(), nullable=True),
        sa.Column("to_assigned_user_id", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "action IN ('check_out', 'check_in')",
            name="ck_equipment_custody_event_action",
        ),
        sa.ForeignKeyConstraint(["equipment_asset_id"], ["equipment_asset.id"]),
        sa.ForeignKeyConstraint(["performed_by_id"], ["user.id"]),
        sa.ForeignKeyConstraint(["from_location_id"], ["location.id"]),
        sa.ForeignKeyConstraint(["to_location_id"], ["location.id"]),
        sa.ForeignKeyConstraint(["from_assigned_user_id"], ["user.id"]),
        sa.ForeignKeyConstraint(["to_assigned_user_id"], ["user.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_equipment_custody_event_asset_id",
        "equipment_custody_event",
        ["equipment_asset_id"],
        unique=False,
    )
    op.create_index(
        "ix_equipment_custody_event_performed_by_id",
        "equipment_custody_event",
        ["performed_by_id"],
        unique=False,
    )
    op.create_index(
        "ix_equipment_custody_event_created_at",
        "equipment_custody_event",
        ["created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_equipment_custody_event_created_at",
        table_name="equipment_custody_event",
    )
    op.drop_index(
        "ix_equipment_custody_event_performed_by_id",
        table_name="equipment_custody_event",
    )
    op.drop_index(
        "ix_equipment_custody_event_asset_id",
        table_name="equipment_custody_event",
    )
    op.drop_table("equipment_custody_event")

    with op.batch_alter_table("equipment_asset") as batch_op:
        batch_op.drop_index("ix_equipment_asset_checked_out_at")
        batch_op.drop_index("ix_equipment_asset_home_location_id")
        batch_op.drop_constraint(
            "ck_equipment_asset_label_qr_target",
            type_="check",
        )
        batch_op.drop_constraint(
            "fk_equipment_asset_home_location_id",
            type_="foreignkey",
        )
        batch_op.drop_column("label_qr_custom_url")
        batch_op.drop_column("label_qr_target")
        batch_op.drop_column("checked_out_at")
        batch_op.drop_column("home_location_id")
