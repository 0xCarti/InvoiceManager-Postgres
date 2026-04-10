"""expand schedule membership role length

Revision ID: a8b9c0d1e2f3
Revises: f7a8b9c0d1e2
Create Date: 2026-04-09 00:00:01.000000

"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "a8b9c0d1e2f3"
down_revision = "f7a8b9c0d1e2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "schedule_user_department_membership",
        "role",
        existing_type=sa.String(length=20),
        type_=sa.String(length=50),
        existing_nullable=False,
        existing_server_default="staff",
    )


def downgrade() -> None:
    op.alter_column(
        "schedule_user_department_membership",
        "role",
        existing_type=sa.String(length=50),
        type_=sa.String(length=20),
        existing_nullable=False,
        existing_server_default="staff",
    )
