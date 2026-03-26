"""add pos sales import soft-delete metadata

Revision ID: 202603260005
Revises: 202603260004
Create Date: 2026-03-26 00:05:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "202603260005"
down_revision = "202603260004"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("pos_sales_import", schema=None) as batch_op:
        batch_op.add_column(sa.Column("deleted_by", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("deleted_at", sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column("deletion_reason", sa.Text(), nullable=True))
        batch_op.create_foreign_key(
            "fk_pos_sales_import_deleted_by_user",
            "user",
            ["deleted_by"],
            ["id"],
        )
        batch_op.create_index(
            "ix_pos_sales_import_deleted_by", ["deleted_by", "deleted_at"], unique=False
        )


def downgrade():
    with op.batch_alter_table("pos_sales_import", schema=None) as batch_op:
        batch_op.drop_index("ix_pos_sales_import_deleted_by")
        batch_op.drop_constraint("fk_pos_sales_import_deleted_by_user", type_="foreignkey")
        batch_op.drop_column("deletion_reason")
        batch_op.drop_column("deleted_at")
        batch_op.drop_column("deleted_by")
