"""add location count submission workflow

Revision ID: b2c3d4e5f6a7
Revises: fa4b5c6d7e8
Create Date: 2026-05-06 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa
import secrets


# revision identifiers, used by Alembic.
revision = "b2c3d4e5f6a7"
down_revision = "fa4b5c6d7e8"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "location",
        sa.Column("count_qr_token", sa.String(length=96), nullable=True),
    )

    connection = op.get_bind()
    location_ids = [
        row[0] for row in connection.execute(sa.text("SELECT id FROM location")).fetchall()
    ]
    seen_tokens: set[str] = set()
    for location_id in location_ids:
        token = secrets.token_urlsafe(24)
        while token in seen_tokens:
            token = secrets.token_urlsafe(24)
        seen_tokens.add(token)
        connection.execute(
            sa.text(
                "UPDATE location SET count_qr_token = :token WHERE id = :location_id"
            ),
            {"token": token, "location_id": location_id},
        )

    op.alter_column("location", "count_qr_token", nullable=False)
    op.create_unique_constraint(
        "uq_location_count_qr_token",
        "location",
        ["count_qr_token"],
    )

    op.create_table(
        "location_count_submission",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("source_location_id", sa.Integer(), nullable=False),
        sa.Column("location_id", sa.Integer(), nullable=True),
        sa.Column("event_location_id", sa.Integer(), nullable=True),
        sa.Column(
            "submission_type",
            sa.String(length=16),
            nullable=False,
            server_default="opening",
        ),
        sa.Column("submitted_name", sa.String(length=120), nullable=False),
        sa.Column("submission_date", sa.Date(), nullable=False),
        sa.Column(
            "status",
            sa.String(length=16),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("review_note", sa.Text(), nullable=True),
        sa.Column("reviewed_by", sa.Integer(), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(), nullable=True),
        sa.Column(
            "submitted_at",
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
        sa.ForeignKeyConstraint(
            ["event_location_id"], ["event_location.id"]
        ),
        sa.ForeignKeyConstraint(["location_id"], ["location.id"]),
        sa.ForeignKeyConstraint(["reviewed_by"], ["user.id"]),
        sa.ForeignKeyConstraint(["source_location_id"], ["location.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "submission_type IN ('opening', 'closing')",
            name="ck_location_count_submission_type",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'approved', 'rejected')",
            name="ck_location_count_submission_status",
        ),
    )
    op.create_index(
        "ix_location_count_submission_status_submitted_at",
        "location_count_submission",
        ["status", "submitted_at"],
    )
    op.create_index(
        "ix_location_count_submission_source_location_date",
        "location_count_submission",
        ["source_location_id", "submission_date"],
    )
    op.create_index(
        "ix_location_count_submission_mapped_location_date",
        "location_count_submission",
        ["location_id", "submission_date"],
    )
    op.create_index(
        "ix_location_count_submission_event_location",
        "location_count_submission",
        ["event_location_id"],
    )

    op.create_table(
        "location_count_submission_row",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("submission_id", sa.Integer(), nullable=False),
        sa.Column("item_id", sa.Integer(), nullable=False),
        sa.Column(
            "count_value",
            sa.Float(),
            nullable=False,
            server_default="0.0",
        ),
        sa.Column("parse_index", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["item_id"],
            ["item.id"],
        ),
        sa.ForeignKeyConstraint(
            ["submission_id"],
            ["location_count_submission.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "submission_id",
            "parse_index",
            name="uq_location_count_submission_row_order",
        ),
    )
    op.create_index(
        "ix_location_count_submission_row_submission",
        "location_count_submission_row",
        ["submission_id"],
    )
    op.create_index(
        "ix_location_count_submission_row_item",
        "location_count_submission_row",
        ["item_id"],
    )


def downgrade():
    op.drop_index(
        "ix_location_count_submission_row_item",
        table_name="location_count_submission_row",
    )
    op.drop_index(
        "ix_location_count_submission_row_submission",
        table_name="location_count_submission_row",
    )
    op.drop_table("location_count_submission_row")

    op.drop_index(
        "ix_location_count_submission_event_location",
        table_name="location_count_submission",
    )
    op.drop_index(
        "ix_location_count_submission_mapped_location_date",
        table_name="location_count_submission",
    )
    op.drop_index(
        "ix_location_count_submission_source_location_date",
        table_name="location_count_submission",
    )
    op.drop_index(
        "ix_location_count_submission_status_submitted_at",
        table_name="location_count_submission",
    )
    op.drop_table("location_count_submission")

    op.drop_constraint("uq_location_count_qr_token", "location", type_="unique")
    op.drop_column("location", "count_qr_token")
