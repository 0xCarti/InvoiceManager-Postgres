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
revision = "202409010001"
down_revision = "202408200002"
branch_labels = None
depends_on = None


MENU_TABLE = "menu"
MENU_PRODUCTS_TABLE = "menu_products"
MENU_ASSIGNMENT_TABLE = "menu_assignment"


def upgrade():
    bind = op.get_bind()
    if not bind:
        return

    if not _has_table(MENU_TABLE, bind):
        op.create_table(
            MENU_TABLE,
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("name", sa.String(length=100), nullable=False, unique=True),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column("last_used_at", sa.DateTime(), nullable=True),
        )

    if not _has_table(MENU_PRODUCTS_TABLE, bind):
        op.create_table(
            MENU_PRODUCTS_TABLE,
            sa.Column("menu_id", sa.Integer(), nullable=False),
            sa.Column("product_id", sa.Integer(), nullable=False),
            sa.ForeignKeyConstraint(["menu_id"], [f"{MENU_TABLE}.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["product_id"], ["product.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("menu_id", "product_id"),
        )

    if not _has_table(MENU_ASSIGNMENT_TABLE, bind):
        op.create_table(
            MENU_ASSIGNMENT_TABLE,
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("menu_id", sa.Integer(), nullable=False),
            sa.Column("location_id", sa.Integer(), nullable=False),
            sa.Column("assigned_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column("unassigned_at", sa.DateTime(), nullable=True),
            sa.ForeignKeyConstraint(["menu_id"], [f"{MENU_TABLE}.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["location_id"], ["location.id"], ondelete="CASCADE"),
        )
        op.create_index(
            "ix_menu_assignment_active",
            MENU_ASSIGNMENT_TABLE,
            ["location_id", "unassigned_at"],
        )

    with op.batch_alter_table("location", recreate="always") as batch_op:
        if not _has_column("location", "current_menu_id", bind):
            batch_op.add_column(
                sa.Column("current_menu_id", sa.Integer(), nullable=True)
            )
            batch_op.create_foreign_key(
                "fk_location_current_menu_id",
                MENU_TABLE,
                ["current_menu_id"],
                ["id"],
                ondelete="SET NULL",
            )


def downgrade():
    bind = op.get_bind()
    if not bind:
        return

    with op.batch_alter_table("location", recreate="always") as batch_op:
        if _has_column("location", "current_menu_id", bind):
            try:
                batch_op.drop_constraint("fk_location_current_menu_id", type_="foreignkey")
            except sa.exc.DBAPIError:
                pass
            batch_op.drop_column("current_menu_id")

    if _has_table(MENU_ASSIGNMENT_TABLE, bind):
        op.drop_index("ix_menu_assignment_active", table_name=MENU_ASSIGNMENT_TABLE)
        op.drop_table(MENU_ASSIGNMENT_TABLE)

    if _has_table(MENU_PRODUCTS_TABLE, bind):
        op.drop_table(MENU_PRODUCTS_TABLE)

    if _has_table(MENU_TABLE, bind):
        op.drop_table(MENU_TABLE)
