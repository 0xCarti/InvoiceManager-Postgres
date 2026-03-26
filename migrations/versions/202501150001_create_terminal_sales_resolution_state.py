from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "202501150001"
down_revision = "202501010001"
branch_labels = None
depends_on = None


def _has_table(table_name: str, bind) -> bool:
    inspector = sa.inspect(bind)
    return inspector.has_table(table_name)


def upgrade():
    bind = op.get_bind()
    if not bind:
        return

    table_name = "terminal_sales_resolution_state"
    if _has_table(table_name, bind):
        return

    op.create_table(
        table_name,
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("event_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("token_id", sa.String(length=128), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.func.now(),
            server_onupdate=sa.func.now(),
        ),
        sa.ForeignKeyConstraint([
            "event_id",
        ], ["event.id"], name="fk_terminal_sales_state_event"),
        sa.ForeignKeyConstraint([
            "user_id",
        ], ["user.id"], name="fk_terminal_sales_state_user"),
        sa.UniqueConstraint(
            "event_id",
            "user_id",
            "token_id",
            name="uq_terminal_sales_state_event_user_token",
        ),
    )
    op.create_index(
        "ix_terminal_sales_state_event_user",
        table_name,
        ["event_id", "user_id"],
    )


def downgrade():
    bind = op.get_bind()
    if not bind:
        return

    table_name = "terminal_sales_resolution_state"
    if not _has_table(table_name, bind):
        return

    op.drop_index("ix_terminal_sales_state_event_user", table_name=table_name)
    op.drop_table(table_name)
