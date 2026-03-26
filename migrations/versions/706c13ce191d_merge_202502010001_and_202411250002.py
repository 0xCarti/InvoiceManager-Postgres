"""Merge 202502010001 and 202411250002

Revision ID: 706c13ce191d
Revises: 202502010001, 202411250002
Create Date: 2025-11-29 01:49:29.963053

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '706c13ce191d'
down_revision = ('202502010001', '202411250002')
branch_labels = None
depends_on = None


def upgrade():
    pass


def downgrade():
    pass
