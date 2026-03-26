"""Create table for storing user filter preferences."""

from alembic import op
import sqlalchemy as sa


def _has_table(table_name: str, bind) -> bool:
    inspector = sa.inspect(bind)
    return inspector.has_table(table_name)


# revision identifiers, used by Alembic.
revision = "202501010001"
down_revision = "202412150001"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    if not bind:
        return

    table_name = "user_filter_preference"
    if _has_table(table_name, bind):
        return

    op.create_table(
        table_name,
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("scope", sa.String(length=255), nullable=False),
        sa.Column(
            "values",
            sa.JSON(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"], name="fk_filter_pref_user"),
    )
    op.create_index(
        "ix_user_filter_preference_scope",
        table_name,
        ["user_id", "scope"],
        unique=True,
    )


def downgrade():
    bind = op.get_bind()
    if not bind:
        return

    table_name = "user_filter_preference"
    if not _has_table(table_name, bind):
        return

    op.drop_index("ix_user_filter_preference_scope", table_name=table_name)
    op.drop_table(table_name)
