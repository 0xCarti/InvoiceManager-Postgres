"""Add notes table for entity annotations."""

import sqlalchemy as sa
from alembic import op


def _has_table(table_name: str, bind) -> bool:
    inspector = sa.inspect(bind)
    return inspector.has_table(table_name)


# revision identifiers, used by Alembic.
revision = "a7c8e9f0b1a2"
down_revision = "f1c2d3e4a5b6"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    if _has_table("note", bind):
        return

    op.create_table(
        "note",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("entity_type", sa.String(length=50), nullable=False),
        sa.Column("entity_id", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("pinned", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column("pinned_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"], name="fk_note_user_id"),
    )
    op.create_index("ix_note_entity", "note", ["entity_type", "entity_id"])
    op.create_index("ix_note_pinned", "note", ["entity_type", "pinned"])


def downgrade():
    bind = op.get_bind()
    if not _has_table("note", bind):
        return

    op.drop_index("ix_note_pinned", table_name="note")
    op.drop_index("ix_note_entity", table_name="note")
    op.drop_table("note")
