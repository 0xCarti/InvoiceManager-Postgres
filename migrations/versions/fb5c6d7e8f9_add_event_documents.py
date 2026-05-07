"""add event documents

Revision ID: fb5c6d7e8f9
Revises: fa4b5c6d7e8
Create Date: 2026-05-06 15:40:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "fb5c6d7e8f9"
down_revision = "fa4b5c6d7e8"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "event_document",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("event_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=True),
        sa.Column("original_filename", sa.String(length=255), nullable=False),
        sa.Column("content_type", sa.String(length=120), nullable=True),
        sa.Column(
            "file_size_bytes",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("storage_path", sa.String(length=1024), nullable=False),
        sa.Column("uploaded_by", sa.Integer(), nullable=True),
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
        ),
        sa.ForeignKeyConstraint(["event_id"], ["event.id"]),
        sa.ForeignKeyConstraint(["uploaded_by"], ["user.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_event_document_event_id", "event_document", ["event_id"], unique=False
    )
    op.create_index(
        "ix_event_document_uploaded_by",
        "event_document",
        ["uploaded_by"],
        unique=False,
    )
    op.create_index(
        "ix_event_document_sha256", "event_document", ["sha256"], unique=False
    )


def downgrade():
    op.drop_index("ix_event_document_sha256", table_name="event_document")
    op.drop_index("ix_event_document_uploaded_by", table_name="event_document")
    op.drop_index("ix_event_document_event_id", table_name="event_document")
    op.drop_table("event_document")
