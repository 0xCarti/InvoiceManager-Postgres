"""expand user password length to 255

Revision ID: c1a2b3d4e5f6
Revises: 84d605cd8180
Create Date: 2026-03-26 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "c1a2b3d4e5f6"
down_revision = "84d605cd8180"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute('ALTER TABLE "user" ALTER COLUMN password TYPE VARCHAR(255)')
    else:
        op.alter_column(
            "user",
            "password",
            existing_type=sa.String(length=80),
            type_=sa.String(length=255),
            existing_nullable=False,
        )


def downgrade():
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute('ALTER TABLE "user" ALTER COLUMN password TYPE VARCHAR(80)')
    else:
        op.alter_column(
            "user",
            "password",
            existing_type=sa.String(length=255),
            type_=sa.String(length=80),
            existing_nullable=False,
        )
