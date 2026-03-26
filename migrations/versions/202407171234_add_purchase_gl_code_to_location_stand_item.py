"""add purchase gl code to location stand item"""

import sqlalchemy as sa
from alembic import op


def _has_table(table_name: str, bind) -> bool:
    """Return True if the database has the given table."""
    inspector = sa.inspect(bind)
    return inspector.has_table(table_name)


def _has_column(table_name: str, column_name: str, bind) -> bool:
    """Return True if the given table already has the specified column."""
    inspector = sa.inspect(bind)
    if not inspector.has_table(table_name):
        return False
    columns = [c["name"] for c in inspector.get_columns(table_name)]
    return column_name in columns


def _has_fk(table_name: str, fk_name: str, bind) -> bool:
    """Return True if the given table already has the specified foreign key."""
    inspector = sa.inspect(bind)
    if not inspector.has_table(table_name):
        return False
    fks = [fk["name"] for fk in inspector.get_foreign_keys(table_name)]
    return fk_name in fks


# revision identifiers, used by Alembic.
revision = "202407171234"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    if not _has_table("location_stand_item", bind):
        return

    has_column = _has_column(
        "location_stand_item", "purchase_gl_code_id", bind
    )
    has_fk = _has_fk(
        "location_stand_item", "fk_location_stand_item_purchase_gl_code", bind
    )

    with op.batch_alter_table(
        "location_stand_item", recreate="always"
    ) as batch_op:
        if not has_column:
            batch_op.add_column(
                sa.Column("purchase_gl_code_id", sa.Integer(), nullable=True)
            )

        if not has_fk:
            batch_op.create_foreign_key(
                "fk_location_stand_item_purchase_gl_code",
                "gl_code",
                ["purchase_gl_code_id"],
                ["id"],
            )


def downgrade():
    bind = op.get_bind()
    if not _has_table("location_stand_item", bind):
        return

    has_fk = _has_fk(
        "location_stand_item", "fk_location_stand_item_purchase_gl_code", bind
    )
    has_column = _has_column(
        "location_stand_item", "purchase_gl_code_id", bind
    )

    with op.batch_alter_table(
        "location_stand_item", recreate="always"
    ) as batch_op:
        if has_fk:
            batch_op.drop_constraint(
                "fk_location_stand_item_purchase_gl_code", type_="foreignkey"
            )

        if has_column:
            batch_op.drop_column("purchase_gl_code_id")
