import sqlalchemy as sa
from alembic import op


def _has_table(table_name: str, bind) -> bool:
    inspector = sa.inspect(bind)
    return inspector.has_table(table_name)


# revision identifiers, used by Alembic.
revision = "202409200001"
down_revision = "202409150001"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    if not bind:
        return

    if _has_table("purchase_invoice_draft", bind):
        return

    op.create_table(
        "purchase_invoice_draft",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("purchase_order_id", sa.Integer(), nullable=False),
        sa.Column("payload", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.ForeignKeyConstraint(["purchase_order_id"], ["purchase_order.id"]),
        sa.UniqueConstraint("purchase_order_id"),
    )


def downgrade():
    bind = op.get_bind()
    if not bind:
        return

    if not _has_table("purchase_invoice_draft", bind):
        return

    op.drop_table("purchase_invoice_draft")
