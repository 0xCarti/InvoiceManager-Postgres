"""Store inventory expiry source ids as strings.

Revision ID: 5e6f7a8b9c0d
Revises: 4d5e6f7a8b9c
Create Date: 2026-08-01
"""

from alembic import op
import sqlalchemy as sa


revision = "5e6f7a8b9c0d"
down_revision = "4d5e6f7a8b9c"
branch_labels = None
depends_on = None


def upgrade():
    op.alter_column(
        "inventory_expiry_lot",
        "source_id",
        existing_type=sa.Integer(),
        type_=sa.String(length=64),
        existing_nullable=True,
        postgresql_using="source_id::varchar",
    )
    op.alter_column(
        "inventory_expiry_lot_adjustment",
        "source_id",
        existing_type=sa.Integer(),
        type_=sa.String(length=64),
        existing_nullable=True,
        postgresql_using="source_id::varchar",
    )


def downgrade():
    op.alter_column(
        "inventory_expiry_lot_adjustment",
        "source_id",
        existing_type=sa.String(length=64),
        type_=sa.Integer(),
        existing_nullable=True,
        postgresql_using=(
            "CASE WHEN source_id ~ '^[0-9]+$' THEN source_id::integer ELSE NULL END"
        ),
    )
    op.alter_column(
        "inventory_expiry_lot",
        "source_id",
        existing_type=sa.String(length=64),
        type_=sa.Integer(),
        existing_nullable=True,
        postgresql_using=(
            "CASE WHEN source_id ~ '^[0-9]+$' THEN source_id::integer ELSE NULL END"
        ),
    )
