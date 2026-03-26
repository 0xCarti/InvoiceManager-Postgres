"""remove user-specific receive location defaults column"""

import sqlalchemy as sa
from alembic import op


def _has_table(table_name: str, bind) -> bool:
    inspector = sa.inspect(bind)
    return inspector.has_table(table_name)


def _has_column(table_name: str, column_name: str, bind) -> bool:
    inspector = sa.inspect(bind)
    if not inspector.has_table(table_name):
        return False
    return column_name in {
        column["name"] for column in inspector.get_columns(table_name)
    }


# revision identifiers, used by Alembic.
revision = "202409150001"
down_revision = "202409010001"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    if not bind or not _has_table("user", bind):
        return

    if not _has_column("user", "receive_location_defaults", bind):
        return

    with op.batch_alter_table("user", recreate="always") as batch_op:
        batch_op.drop_column("receive_location_defaults")


def downgrade():
    bind = op.get_bind()
    if not bind or not _has_table("user", bind):
        return

    if _has_column("user", "receive_location_defaults", bind):
        return

    with op.batch_alter_table("user", recreate="always") as batch_op:
        batch_op.add_column(
            sa.Column(
                "receive_location_defaults",
                sa.Text(),
                nullable=False,
                server_default="",
            )
        )

    with op.batch_alter_table("user", recreate="always") as batch_op:
        batch_op.alter_column(
            "receive_location_defaults", server_default=None
        )
