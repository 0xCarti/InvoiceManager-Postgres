"""add sold_at to terminal sale

Revision ID: c2f321f4c8b5
Revises: bbdaf2ebdf4c
Create Date: 2025-01-01 00:00:00.000000

"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "c2f321f4c8b5"
down_revision = "bbdaf2ebdf4c"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("terminal_sale", recreate="always") as batch_op:
        batch_op.add_column(
            sa.Column(
                "sold_at",
                sa.DateTime(),
                nullable=False,
                server_default=sa.text("CURRENT_TIMESTAMP"),
            )
        )
        batch_op.alter_column("sold_at", server_default=None)


def downgrade():
    op.drop_column("terminal_sale", "sold_at")
