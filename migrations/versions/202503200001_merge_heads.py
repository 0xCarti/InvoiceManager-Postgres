"""Merge heads 706c13ce191d and 202503050001

Revision ID: 202503200001
Revises: 706c13ce191d, 202503050001
Create Date: 2025-03-20 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "202503200001"
down_revision = ("706c13ce191d", "202503050001")
branch_labels = None
depends_on = None


def upgrade():
    pass


def downgrade():
    pass
