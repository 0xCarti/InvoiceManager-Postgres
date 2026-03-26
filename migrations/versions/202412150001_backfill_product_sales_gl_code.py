"""Backfill product sales GL code from GL code id."""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "202412150001"
down_revision = "202412010002"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    if not bind:
        return

    op.execute(
        sa.text(
            """
            UPDATE product
            SET sales_gl_code_id = gl_code_id
            WHERE sales_gl_code_id IS NULL AND gl_code_id IS NOT NULL
            """
        )
    )


def downgrade():
    bind = op.get_bind()
    if not bind:
        return

    op.execute(
        sa.text(
            """
            UPDATE product
            SET sales_gl_code_id = NULL
            WHERE sales_gl_code_id = gl_code_id
            """
        )
    )
