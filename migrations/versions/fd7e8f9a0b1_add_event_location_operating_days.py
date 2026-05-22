"""add event location operating days

Revision ID: fd7e8f9a0b1
Revises: fc6d7e8f9a0
Create Date: 2026-05-22 12:00:00.000000
"""

from datetime import timedelta

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "fd7e8f9a0b1"
down_revision = "fc6d7e8f9a0"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "event_location_operating_day",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("event_location_id", sa.Integer(), nullable=False),
        sa.Column("operating_date", sa.Date(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["event_location_id"],
            ["event_location.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "event_location_id",
            "operating_date",
            name="uq_event_location_operating_day",
        ),
    )
    op.create_index(
        "ix_event_location_operating_day_date",
        "event_location_operating_day",
        ["operating_date"],
    )

    op.add_column(
        "location_count_submission",
        sa.Column("event_operating_day_id", sa.Integer(), nullable=True),
    )
    op.add_column(
        "location_count_submission",
        sa.Column(
            "applied_count_source",
            sa.String(length=16),
            server_default="submitted",
            nullable=False,
        ),
    )
    op.create_foreign_key(
        "fk_location_count_submission_event_operating_day",
        "location_count_submission",
        "event_location_operating_day",
        ["event_operating_day_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_check_constraint(
        "ck_location_count_submission_applied_count_source",
        "location_count_submission",
        "applied_count_source IN ('submitted', 'expected')",
    )
    op.create_index(
        "ix_location_count_submission_event_operating_day",
        "location_count_submission",
        ["event_operating_day_id"],
    )

    op.add_column(
        "location_count_submission_row",
        sa.Column("submitted_count_value", sa.Float(), nullable=True),
    )
    op.add_column(
        "location_count_submission_row",
        sa.Column("expected_count_value", sa.Float(), nullable=True),
    )

    bind = op.get_bind()
    event_table = sa.table(
        "event",
        sa.column("id", sa.Integer),
        sa.column("start_date", sa.Date),
        sa.column("end_date", sa.Date),
    )
    event_location_table = sa.table(
        "event_location",
        sa.column("id", sa.Integer),
        sa.column("event_id", sa.Integer),
    )
    operating_day_table = sa.table(
        "event_location_operating_day",
        sa.column("id", sa.Integer),
        sa.column("event_location_id", sa.Integer),
        sa.column("operating_date", sa.Date),
    )
    submission_table = sa.table(
        "location_count_submission",
        sa.column("id", sa.Integer),
        sa.column("event_location_id", sa.Integer),
        sa.column("event_operating_day_id", sa.Integer),
        sa.column("submission_date", sa.Date),
    )
    row_table = sa.table(
        "location_count_submission_row",
        sa.column("id", sa.Integer),
        sa.column("count_value", sa.Float),
        sa.column("submitted_count_value", sa.Float),
    )

    events_by_id = {
        row.id: row
        for row in bind.execute(
            sa.select(
                event_table.c.id,
                event_table.c.start_date,
                event_table.c.end_date,
            )
        )
    }
    operating_day_ids: dict[tuple[int, object], int] = {}
    for event_location in bind.execute(
        sa.select(event_location_table.c.id, event_location_table.c.event_id)
    ):
        event_row = events_by_id.get(event_location.event_id)
        if (
            event_row is None
            or event_row.start_date is None
            or event_row.end_date is None
        ):
            continue
        start_date = min(event_row.start_date, event_row.end_date)
        end_date = max(event_row.start_date, event_row.end_date)
        current_date = start_date
        while current_date <= end_date:
            result = bind.execute(
                operating_day_table.insert().values(
                    event_location_id=event_location.id,
                    operating_date=current_date,
                )
            )
            operating_day_ids[(event_location.id, current_date)] = (
                result.inserted_primary_key[0]
            )
            current_date = current_date + timedelta(days=1)

    for submission in bind.execute(
        sa.select(
            submission_table.c.id,
            submission_table.c.event_location_id,
            submission_table.c.submission_date,
        ).where(submission_table.c.event_location_id.is_not(None))
    ):
        operating_day_id = operating_day_ids.get(
            (submission.event_location_id, submission.submission_date)
        )
        if operating_day_id is None:
            continue
        bind.execute(
            submission_table.update()
            .where(submission_table.c.id == submission.id)
            .values(event_operating_day_id=operating_day_id)
        )

    bind.execute(
        row_table.update().values(
            submitted_count_value=row_table.c.count_value,
        )
    )


def downgrade():
    op.drop_column("location_count_submission_row", "expected_count_value")
    op.drop_column("location_count_submission_row", "submitted_count_value")

    op.drop_index(
        "ix_location_count_submission_event_operating_day",
        table_name="location_count_submission",
    )
    op.drop_constraint(
        "ck_location_count_submission_applied_count_source",
        "location_count_submission",
        type_="check",
    )
    op.drop_constraint(
        "fk_location_count_submission_event_operating_day",
        "location_count_submission",
        type_="foreignkey",
    )
    op.drop_column("location_count_submission", "applied_count_source")
    op.drop_column("location_count_submission", "event_operating_day_id")

    op.drop_index(
        "ix_event_location_operating_day_date",
        table_name="event_location_operating_day",
    )
    op.drop_table("event_location_operating_day")
