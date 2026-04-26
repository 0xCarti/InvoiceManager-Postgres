"""add equipment maintenance workflow

Revision ID: f0a1b2c3d4e5
Revises: e7f8a9b0c1d2
Create Date: 2026-04-25 11:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "f0a1b2c3d4e5"
down_revision = "e7f8a9b0c1d2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("equipment_asset") as batch_op:
        batch_op.add_column(
            sa.Column("service_contract_name", sa.String(length=120), nullable=True)
        )
        batch_op.add_column(
            sa.Column(
                "service_contract_reference", sa.String(length=120), nullable=True
            )
        )
        batch_op.add_column(
            sa.Column("service_contract_expires_on", sa.Date(), nullable=True)
        )
        batch_op.add_column(
            sa.Column("service_contract_notes", sa.Text(), nullable=True)
        )
        batch_op.add_column(
            sa.Column("service_interval_days", sa.Integer(), nullable=True)
        )
        batch_op.add_column(sa.Column("last_service_on", sa.Date(), nullable=True))
        batch_op.add_column(sa.Column("next_service_due_on", sa.Date(), nullable=True))
        batch_op.create_index(
            "ix_equipment_asset_service_contract_expires_on",
            ["service_contract_expires_on"],
            unique=False,
        )
        batch_op.create_index(
            "ix_equipment_asset_next_service_due_on",
            ["next_service_due_on"],
            unique=False,
        )

    op.create_table(
        "equipment_maintenance_issue",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("equipment_asset_id", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "priority",
            sa.String(length=20),
            nullable=False,
            server_default="medium",
        ),
        sa.Column(
            "status",
            sa.String(length=20),
            nullable=False,
            server_default="open",
        ),
        sa.Column(
            "reported_on",
            sa.Date(),
            nullable=False,
            server_default=sa.func.current_date(),
        ),
        sa.Column("due_on", sa.Date(), nullable=True),
        sa.Column("assigned_user_id", sa.Integer(), nullable=True),
        sa.Column("assigned_vendor_id", sa.Integer(), nullable=True),
        sa.Column("parts_cost", sa.Float(), nullable=True),
        sa.Column("labor_cost", sa.Float(), nullable=True),
        sa.Column("downtime_started_on", sa.Date(), nullable=True),
        sa.Column("downtime_resolved_on", sa.Date(), nullable=True),
        sa.Column("resolved_on", sa.Date(), nullable=True),
        sa.Column("resolution_summary", sa.Text(), nullable=True),
        sa.Column("reopened_count", sa.Integer(), nullable=False, server_default="0"),
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
            "priority IN ('low', 'medium', 'high', 'critical')",
            name="ck_equipment_maintenance_issue_priority",
        ),
        sa.CheckConstraint(
            "status IN ('open', 'in_progress', 'waiting_vendor', 'resolved', 'cancelled')",
            name="ck_equipment_maintenance_issue_status",
        ),
        sa.ForeignKeyConstraint(["assigned_user_id"], ["user.id"]),
        sa.ForeignKeyConstraint(["assigned_vendor_id"], ["vendor.id"]),
        sa.ForeignKeyConstraint(["created_by_id"], ["user.id"]),
        sa.ForeignKeyConstraint(["equipment_asset_id"], ["equipment_asset.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_equipment_maintenance_issue_asset_id",
        "equipment_maintenance_issue",
        ["equipment_asset_id"],
        unique=False,
    )
    op.create_index(
        "ix_equipment_maintenance_issue_status",
        "equipment_maintenance_issue",
        ["status"],
        unique=False,
    )
    op.create_index(
        "ix_equipment_maintenance_issue_priority",
        "equipment_maintenance_issue",
        ["priority"],
        unique=False,
    )
    op.create_index(
        "ix_equipment_maintenance_issue_due_on",
        "equipment_maintenance_issue",
        ["due_on"],
        unique=False,
    )
    op.create_index(
        "ix_equipment_maintenance_issue_assigned_user",
        "equipment_maintenance_issue",
        ["assigned_user_id"],
        unique=False,
    )
    op.create_index(
        "ix_equipment_maintenance_issue_assigned_vendor",
        "equipment_maintenance_issue",
        ["assigned_vendor_id"],
        unique=False,
    )
    op.create_index(
        "ix_equipment_maintenance_issue_created_by",
        "equipment_maintenance_issue",
        ["created_by_id"],
        unique=False,
    )

    op.create_table(
        "equipment_maintenance_update",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("issue_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column(
            "event_type",
            sa.String(length=32),
            nullable=False,
            server_default="comment",
        ),
        sa.Column("message", sa.Text(), nullable=True),
        sa.Column("previous_status", sa.String(length=20), nullable=True),
        sa.Column("new_status", sa.String(length=20), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "event_type IN ('created', 'edited', 'comment', 'status_changed')",
            name="ck_equipment_maintenance_update_event_type",
        ),
        sa.ForeignKeyConstraint(
            ["issue_id"], ["equipment_maintenance_issue.id"]
        ),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_equipment_maintenance_update_issue_id",
        "equipment_maintenance_update",
        ["issue_id"],
        unique=False,
    )
    op.create_index(
        "ix_equipment_maintenance_update_user_id",
        "equipment_maintenance_update",
        ["user_id"],
        unique=False,
    )
    op.create_index(
        "ix_equipment_maintenance_update_created_at",
        "equipment_maintenance_update",
        ["created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_equipment_maintenance_update_created_at",
        table_name="equipment_maintenance_update",
    )
    op.drop_index(
        "ix_equipment_maintenance_update_user_id",
        table_name="equipment_maintenance_update",
    )
    op.drop_index(
        "ix_equipment_maintenance_update_issue_id",
        table_name="equipment_maintenance_update",
    )
    op.drop_table("equipment_maintenance_update")

    op.drop_index(
        "ix_equipment_maintenance_issue_created_by",
        table_name="equipment_maintenance_issue",
    )
    op.drop_index(
        "ix_equipment_maintenance_issue_assigned_vendor",
        table_name="equipment_maintenance_issue",
    )
    op.drop_index(
        "ix_equipment_maintenance_issue_assigned_user",
        table_name="equipment_maintenance_issue",
    )
    op.drop_index(
        "ix_equipment_maintenance_issue_due_on",
        table_name="equipment_maintenance_issue",
    )
    op.drop_index(
        "ix_equipment_maintenance_issue_priority",
        table_name="equipment_maintenance_issue",
    )
    op.drop_index(
        "ix_equipment_maintenance_issue_status",
        table_name="equipment_maintenance_issue",
    )
    op.drop_index(
        "ix_equipment_maintenance_issue_asset_id",
        table_name="equipment_maintenance_issue",
    )
    op.drop_table("equipment_maintenance_issue")

    with op.batch_alter_table("equipment_asset") as batch_op:
        batch_op.drop_index("ix_equipment_asset_next_service_due_on")
        batch_op.drop_index("ix_equipment_asset_service_contract_expires_on")
        batch_op.drop_column("next_service_due_on")
        batch_op.drop_column("last_service_on")
        batch_op.drop_column("service_interval_days")
        batch_op.drop_column("service_contract_notes")
        batch_op.drop_column("service_contract_expires_on")
        batch_op.drop_column("service_contract_reference")
        batch_op.drop_column("service_contract_name")
