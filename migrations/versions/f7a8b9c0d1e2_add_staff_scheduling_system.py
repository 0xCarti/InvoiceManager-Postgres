"""add staff scheduling system

Revision ID: f7a8b9c0d1e2
Revises: e1f2a3b4c5d6
Create Date: 2026-04-09 00:00:00.000000

"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "f7a8b9c0d1e2"
down_revision = "e1f2a3b4c5d6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "user",
        sa.Column("hourly_rate", sa.Float(), nullable=True, server_default="0.0"),
    )
    op.add_column(
        "user",
        sa.Column(
            "desired_weekly_hours",
            sa.Float(),
            nullable=True,
            server_default="0.0",
        ),
    )
    op.add_column(
        "user",
        sa.Column(
            "max_weekly_hours",
            sa.Float(),
            nullable=True,
            server_default="0.0",
        ),
    )
    op.add_column(
        "user",
        sa.Column(
            "schedule_enabled",
            sa.Boolean(),
            nullable=False,
            server_default="1",
        ),
    )
    op.add_column("user", sa.Column("schedule_notes", sa.Text(), nullable=True))
    op.add_column(
        "user",
        sa.Column(
            "notify_schedule_post_email",
            sa.Boolean(),
            nullable=False,
            server_default="1",
        ),
    )
    op.add_column(
        "user",
        sa.Column(
            "notify_schedule_post_text",
            sa.Boolean(),
            nullable=False,
            server_default="0",
        ),
    )
    op.add_column(
        "user",
        sa.Column(
            "notify_schedule_changes_email",
            sa.Boolean(),
            nullable=False,
            server_default="1",
        ),
    )
    op.add_column(
        "user",
        sa.Column(
            "notify_schedule_changes_text",
            sa.Boolean(),
            nullable=False,
            server_default="0",
        ),
    )
    op.add_column(
        "user",
        sa.Column(
            "notify_tradeboard_email",
            sa.Boolean(),
            nullable=False,
            server_default="1",
        ),
    )
    op.add_column(
        "user",
        sa.Column(
            "notify_tradeboard_text",
            sa.Boolean(),
            nullable=False,
            server_default="0",
        ),
    )

    op.create_table(
        "schedule_department",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )
    op.create_index(
        "ix_schedule_department_active",
        "schedule_department",
        ["active"],
        unique=False,
    )

    op.create_table(
        "schedule_shift_position",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("department_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("default_color", sa.String(length=20), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False, server_default="1"),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["department_id"], ["schedule_department.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "department_id",
            "name",
            name="uq_schedule_shift_position_department_name",
        ),
    )
    op.create_index(
        "ix_schedule_shift_position_department",
        "schedule_shift_position",
        ["department_id"],
        unique=False,
    )
    op.create_index(
        "ix_schedule_shift_position_active",
        "schedule_shift_position",
        ["active"],
        unique=False,
    )

    op.create_table(
        "schedule_user_department_membership",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("department_id", sa.Integer(), nullable=False),
        sa.Column(
            "role",
            sa.String(length=20),
            nullable=False,
            server_default="staff",
        ),
        sa.Column("is_primary", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("reports_to_user_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["department_id"], ["schedule_department.id"]),
        sa.ForeignKeyConstraint(["reports_to_user_id"], ["user.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "user_id",
            "department_id",
            name="uq_schedule_user_department_membership_user_department",
        ),
    )
    op.create_index(
        "ix_schedule_user_department_membership_department",
        "schedule_user_department_membership",
        ["department_id"],
        unique=False,
    )
    op.create_index(
        "ix_schedule_user_department_membership_reports_to",
        "schedule_user_department_membership",
        ["reports_to_user_id"],
        unique=False,
    )

    op.create_table(
        "schedule_user_position_eligibility",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("position_id", sa.Integer(), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("active", sa.Boolean(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["position_id"], ["schedule_shift_position.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "user_id",
            "position_id",
            name="uq_schedule_user_position_eligibility_user_position",
        ),
    )
    op.create_index(
        "ix_schedule_user_position_eligibility_position",
        "schedule_user_position_eligibility",
        ["position_id"],
        unique=False,
    )

    op.create_table(
        "schedule_department_week",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("department_id", sa.Integer(), nullable=False),
        sa.Column("week_start", sa.Date(), nullable=False),
        sa.Column("is_published", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("current_version", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("published_at", sa.DateTime(), nullable=True),
        sa.Column("unpublished_at", sa.DateTime(), nullable=True),
        sa.Column("published_by_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["department_id"], ["schedule_department.id"]),
        sa.ForeignKeyConstraint(["published_by_id"], ["user.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "department_id",
            "week_start",
            name="uq_schedule_department_week_department_week_start",
        ),
    )
    op.create_index(
        "ix_schedule_department_week_published",
        "schedule_department_week",
        ["is_published"],
        unique=False,
    )

    op.create_table(
        "schedule_shift",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("schedule_week_id", sa.Integer(), nullable=False),
        sa.Column("position_id", sa.Integer(), nullable=False),
        sa.Column("assigned_user_id", sa.Integer(), nullable=True),
        sa.Column("location_id", sa.Integer(), nullable=True),
        sa.Column("event_id", sa.Integer(), nullable=True),
        sa.Column("shift_date", sa.Date(), nullable=False),
        sa.Column("start_time", sa.Time(), nullable=False),
        sa.Column("end_time", sa.Time(), nullable=False),
        sa.Column("paid_hours", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("paid_hours_manual", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("color", sa.String(length=20), nullable=True),
        sa.Column(
            "assignment_mode",
            sa.String(length=20),
            nullable=False,
            server_default="assigned",
        ),
        sa.Column("is_locked", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column(
            "hourly_rate_snapshot",
            sa.Float(),
            nullable=True,
            server_default="0.0",
        ),
        sa.Column("live_version", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_by_id", sa.Integer(), nullable=True),
        sa.Column("updated_by_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["assigned_user_id"], ["user.id"]),
        sa.ForeignKeyConstraint(["created_by_id"], ["user.id"]),
        sa.ForeignKeyConstraint(["event_id"], ["event.id"]),
        sa.ForeignKeyConstraint(["location_id"], ["location.id"]),
        sa.ForeignKeyConstraint(["position_id"], ["schedule_shift_position.id"]),
        sa.ForeignKeyConstraint(["schedule_week_id"], ["schedule_department_week.id"]),
        sa.ForeignKeyConstraint(["updated_by_id"], ["user.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_schedule_shift_week_date",
        "schedule_shift",
        ["schedule_week_id", "shift_date"],
        unique=False,
    )
    op.create_index(
        "ix_schedule_shift_assigned_user",
        "schedule_shift",
        ["assigned_user_id"],
        unique=False,
    )
    op.create_index(
        "ix_schedule_shift_position",
        "schedule_shift",
        ["position_id"],
        unique=False,
    )
    op.create_index(
        "ix_schedule_shift_assignment_mode",
        "schedule_shift",
        ["assignment_mode"],
        unique=False,
    )

    op.create_table(
        "schedule_shift_audit",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("shift_id", sa.Integer(), nullable=False),
        sa.Column("action", sa.String(length=50), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("details", sa.JSON(), nullable=True),
        sa.Column("changed_by_user_id", sa.Integer(), nullable=True),
        sa.Column("changed_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["changed_by_user_id"], ["user.id"]),
        sa.ForeignKeyConstraint(["shift_id"], ["schedule_shift.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_schedule_shift_audit_shift",
        "schedule_shift_audit",
        ["shift_id"],
        unique=False,
    )
    op.create_index(
        "ix_schedule_shift_audit_changed_at",
        "schedule_shift_audit",
        ["changed_at"],
        unique=False,
    )

    op.create_table(
        "schedule_recurring_availability",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("weekday", sa.Integer(), nullable=False),
        sa.Column("start_time", sa.Time(), nullable=False),
        sa.Column("end_time", sa.Time(), nullable=False),
        sa.Column("note", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_schedule_recurring_availability_user_weekday",
        "schedule_recurring_availability",
        ["user_id", "weekday"],
        unique=False,
    )

    op.create_table(
        "schedule_availability_override",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("start_at", sa.DateTime(), nullable=False),
        sa.Column("end_at", sa.DateTime(), nullable=False),
        sa.Column("is_available", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("note", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_schedule_availability_override_user",
        "schedule_availability_override",
        ["user_id"],
        unique=False,
    )
    op.create_index(
        "ix_schedule_availability_override_start",
        "schedule_availability_override",
        ["start_at"],
        unique=False,
    )

    op.create_table(
        "schedule_time_off_request",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=False),
        sa.Column("start_time", sa.Time(), nullable=True),
        sa.Column("end_time", sa.Time(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("manager_note", sa.Text(), nullable=True),
        sa.Column(
            "status",
            sa.String(length=20),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("reviewed_by_id", sa.Integer(), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["reviewed_by_id"], ["user.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_schedule_time_off_request_user",
        "schedule_time_off_request",
        ["user_id"],
        unique=False,
    )
    op.create_index(
        "ix_schedule_time_off_request_status",
        "schedule_time_off_request",
        ["status"],
        unique=False,
    )
    op.create_index(
        "ix_schedule_time_off_request_start_end",
        "schedule_time_off_request",
        ["start_date", "end_date"],
        unique=False,
    )

    op.create_table(
        "schedule_week_view_receipt",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("schedule_week_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(), nullable=True),
        sa.Column("last_seen_at", sa.DateTime(), nullable=True),
        sa.Column("last_seen_version", sa.Integer(), nullable=False, server_default="0"),
        sa.ForeignKeyConstraint(["schedule_week_id"], ["schedule_department_week.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "schedule_week_id",
            "user_id",
            name="uq_schedule_week_view_receipt_week_user",
        ),
    )
    op.create_index(
        "ix_schedule_week_view_receipt_user",
        "schedule_week_view_receipt",
        ["user_id"],
        unique=False,
    )

    op.create_table(
        "schedule_tradeboard_claim",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("shift_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column(
            "status",
            sa.String(length=20),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("manager_note", sa.Text(), nullable=True),
        sa.Column("reviewed_by_id", sa.Integer(), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["reviewed_by_id"], ["user.id"]),
        sa.ForeignKeyConstraint(["shift_id"], ["schedule_shift.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "shift_id",
            "user_id",
            name="uq_schedule_tradeboard_claim_shift_user",
        ),
    )
    op.create_index(
        "ix_schedule_tradeboard_claim_status",
        "schedule_tradeboard_claim",
        ["status"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_schedule_tradeboard_claim_status", table_name="schedule_tradeboard_claim")
    op.drop_table("schedule_tradeboard_claim")

    op.drop_index("ix_schedule_week_view_receipt_user", table_name="schedule_week_view_receipt")
    op.drop_table("schedule_week_view_receipt")

    op.drop_index("ix_schedule_time_off_request_start_end", table_name="schedule_time_off_request")
    op.drop_index("ix_schedule_time_off_request_status", table_name="schedule_time_off_request")
    op.drop_index("ix_schedule_time_off_request_user", table_name="schedule_time_off_request")
    op.drop_table("schedule_time_off_request")

    op.drop_index("ix_schedule_availability_override_start", table_name="schedule_availability_override")
    op.drop_index("ix_schedule_availability_override_user", table_name="schedule_availability_override")
    op.drop_table("schedule_availability_override")

    op.drop_index("ix_schedule_recurring_availability_user_weekday", table_name="schedule_recurring_availability")
    op.drop_table("schedule_recurring_availability")

    op.drop_index("ix_schedule_shift_audit_changed_at", table_name="schedule_shift_audit")
    op.drop_index("ix_schedule_shift_audit_shift", table_name="schedule_shift_audit")
    op.drop_table("schedule_shift_audit")

    op.drop_index("ix_schedule_shift_assignment_mode", table_name="schedule_shift")
    op.drop_index("ix_schedule_shift_position", table_name="schedule_shift")
    op.drop_index("ix_schedule_shift_assigned_user", table_name="schedule_shift")
    op.drop_index("ix_schedule_shift_week_date", table_name="schedule_shift")
    op.drop_table("schedule_shift")

    op.drop_index("ix_schedule_department_week_published", table_name="schedule_department_week")
    op.drop_table("schedule_department_week")

    op.drop_index("ix_schedule_user_position_eligibility_position", table_name="schedule_user_position_eligibility")
    op.drop_table("schedule_user_position_eligibility")

    op.drop_index("ix_schedule_user_department_membership_reports_to", table_name="schedule_user_department_membership")
    op.drop_index("ix_schedule_user_department_membership_department", table_name="schedule_user_department_membership")
    op.drop_table("schedule_user_department_membership")

    op.drop_index("ix_schedule_shift_position_active", table_name="schedule_shift_position")
    op.drop_index("ix_schedule_shift_position_department", table_name="schedule_shift_position")
    op.drop_table("schedule_shift_position")

    op.drop_index("ix_schedule_department_active", table_name="schedule_department")
    op.drop_table("schedule_department")

    op.drop_column("user", "notify_tradeboard_text")
    op.drop_column("user", "notify_tradeboard_email")
    op.drop_column("user", "notify_schedule_changes_text")
    op.drop_column("user", "notify_schedule_changes_email")
    op.drop_column("user", "notify_schedule_post_text")
    op.drop_column("user", "notify_schedule_post_email")
    op.drop_column("user", "schedule_notes")
    op.drop_column("user", "schedule_enabled")
    op.drop_column("user", "max_weekly_hours")
    op.drop_column("user", "desired_weekly_hours")
    op.drop_column("user", "hourly_rate")
