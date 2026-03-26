"""allow duplicate item unit names

Revision ID: 4bde7cda3c1c
Revises: 3b2f1c9d5e6a
Create Date: 2025-02-05 00:00:00.000000

"""

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision = "4bde7cda3c1c"
down_revision = "3b2f1c9d5e6a"
branch_labels = None
depends_on = None


def _has_table(table_name: str, bind) -> bool:
    inspector = sa.inspect(bind)
    return inspector.has_table(table_name)


def _has_unique_constraint(table_name: str, constraint_name: str, bind) -> bool:
    inspector = sa.inspect(bind)
    if not inspector.has_table(table_name):
        return False
    return any(
        constraint["name"] == constraint_name
        for constraint in inspector.get_unique_constraints(table_name)
    )


def upgrade():
    bind = op.get_bind()
    table_name = "item_unit"
    constraint_name = "_item_unit_name_uc"

    if not _has_table(table_name, bind) or not _has_unique_constraint(
        table_name, constraint_name, bind
    ):
        return

    with op.batch_alter_table(table_name, recreate="always") as batch_op:
        batch_op.drop_constraint(constraint_name, type_="unique")


def downgrade():
    bind = op.get_bind()
    table_name = "item_unit"
    constraint_name = "_item_unit_name_uc"

    if not _has_table(table_name, bind) or _has_unique_constraint(
        table_name, constraint_name, bind
    ):
        return

    with op.batch_alter_table(table_name, recreate="always") as batch_op:
        batch_op.create_unique_constraint(constraint_name, ["item_id", "name"])
