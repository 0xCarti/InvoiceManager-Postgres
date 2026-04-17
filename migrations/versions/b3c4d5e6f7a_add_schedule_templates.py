"""add schedule templates

Revision ID: b3c4d5e6f7a
Revises: b2c3d4e5f6a
Create Date: 2026-04-17 00:00:00.000000

"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "b3c4d5e6f7a"
down_revision = "b2c3d4e5f6a"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "schedule_template",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("department_id", sa.Integer(), nullable=False),
        sa.Column("position_id", sa.Integer(), nullable=False),
        sa.Column(
            "span",
            sa.String(length=20),
            nullable=False,
            server_default="week",
        ),
        sa.Column("active", sa.Boolean(), nullable=False, server_default="1"),
        sa.Column("created_by_id", sa.Integer(), nullable=True),
        sa.Column("updated_by_id", sa.Integer(), nullable=True),
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
        sa.ForeignKeyConstraint(["created_by_id"], ["user.id"]),
        sa.ForeignKeyConstraint(["department_id"], ["schedule_department.id"]),
        sa.ForeignKeyConstraint(["position_id"], ["schedule_shift_position.id"]),
        sa.ForeignKeyConstraint(["updated_by_id"], ["user.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_schedule_template_department",
        "schedule_template",
        ["department_id"],
        unique=False,
    )
    op.create_index(
        "ix_schedule_template_position",
        "schedule_template",
        ["position_id"],
        unique=False,
    )
    op.create_index(
        "ix_schedule_template_span_active",
        "schedule_template",
        ["span", "active"],
        unique=False,
    )

    op.create_table(
        "schedule_template_entry",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("template_id", sa.Integer(), nullable=False),
        sa.Column("weekday", sa.Integer(), nullable=True),
        sa.Column("day_of_month", sa.Integer(), nullable=True),
        sa.Column("month_of_year", sa.Integer(), nullable=True),
        sa.Column(
            "assignment_mode",
            sa.String(length=20),
            nullable=False,
            server_default="assigned",
        ),
        sa.Column("assigned_user_id", sa.Integer(), nullable=True),
        sa.Column("start_time", sa.Time(), nullable=False),
        sa.Column("end_time", sa.Time(), nullable=False),
        sa.Column("paid_hours", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column(
            "paid_hours_manual",
            sa.Boolean(),
            nullable=False,
            server_default="0",
        ),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("color", sa.String(length=20), nullable=True),
        sa.Column("is_locked", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("created_by_id", sa.Integer(), nullable=True),
        sa.Column("updated_by_id", sa.Integer(), nullable=True),
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
        sa.ForeignKeyConstraint(["assigned_user_id"], ["user.id"]),
        sa.ForeignKeyConstraint(["created_by_id"], ["user.id"]),
        sa.ForeignKeyConstraint(["template_id"], ["schedule_template.id"]),
        sa.ForeignKeyConstraint(["updated_by_id"], ["user.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_schedule_template_entry_template",
        "schedule_template_entry",
        ["template_id"],
        unique=False,
    )
    op.create_index(
        "ix_schedule_template_entry_assigned_user",
        "schedule_template_entry",
        ["assigned_user_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_schedule_template_entry_assigned_user",
        table_name="schedule_template_entry",
    )
    op.drop_index(
        "ix_schedule_template_entry_template",
        table_name="schedule_template_entry",
    )
    op.drop_table("schedule_template_entry")

    op.drop_index("ix_schedule_template_span_active", table_name="schedule_template")
    op.drop_index("ix_schedule_template_position", table_name="schedule_template")
    op.drop_index("ix_schedule_template_department", table_name="schedule_template")
    op.drop_table("schedule_template")
