"""add internal communications

Revision ID: b1c2d3e4f5a6
Revises: a8b9c0d1e2f3
Create Date: 2026-04-09 00:00:02.000000

"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "b1c2d3e4f5a6"
down_revision = "a8b9c0d1e2f3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "communication",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column(
            "kind",
            sa.String(length=20),
            nullable=False,
            server_default="message",
        ),
        sa.Column("sender_id", sa.Integer(), nullable=False),
        sa.Column("department_id", sa.Integer(), nullable=True),
        sa.Column("audience_type", sa.String(length=20), nullable=False),
        sa.Column("subject", sa.String(length=200), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column(
            "pinned",
            sa.Boolean(),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "active",
            sa.Boolean(),
            nullable=False,
            server_default="1",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.ForeignKeyConstraint(["department_id"], ["schedule_department.id"]),
        sa.ForeignKeyConstraint(["sender_id"], ["user.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_communication_kind_created",
        "communication",
        ["kind", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_communication_department",
        "communication",
        ["department_id"],
        unique=False,
    )
    op.create_index(
        "ix_communication_sender",
        "communication",
        ["sender_id"],
        unique=False,
    )
    op.create_index(
        "ix_communication_active_pinned",
        "communication",
        ["active", "pinned"],
        unique=False,
    )

    op.create_table(
        "communication_recipient",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("communication_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("read_at", sa.DateTime(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.ForeignKeyConstraint(["communication_id"], ["communication.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "communication_id",
            "user_id",
            name="uq_communication_recipient_communication_user",
        ),
    )
    op.create_index(
        "ix_communication_recipient_user",
        "communication_recipient",
        ["user_id"],
        unique=False,
    )
    op.create_index(
        "ix_communication_recipient_user_read",
        "communication_recipient",
        ["user_id", "read_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_communication_recipient_user_read",
        table_name="communication_recipient",
    )
    op.drop_index("ix_communication_recipient_user", table_name="communication_recipient")
    op.drop_table("communication_recipient")

    op.drop_index("ix_communication_active_pinned", table_name="communication")
    op.drop_index("ix_communication_sender", table_name="communication")
    op.drop_index("ix_communication_department", table_name="communication")
    op.drop_index("ix_communication_kind_created", table_name="communication")
    op.drop_table("communication")
