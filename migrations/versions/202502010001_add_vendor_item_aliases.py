from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "202502010001"
down_revision = "202502050001"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if inspector.has_table("vendor_item_alias"):
        return

    op.create_table(
        "vendor_item_alias",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("vendor_id", sa.Integer(), sa.ForeignKey("vendor.id"), nullable=False),
        sa.Column("item_id", sa.Integer(), sa.ForeignKey("item.id"), nullable=False),
        sa.Column(
            "item_unit_id", sa.Integer(), sa.ForeignKey("item_unit.id"), nullable=True
        ),
        sa.Column("vendor_sku", sa.String(length=100), nullable=True),
        sa.Column("vendor_description", sa.String(length=255), nullable=True),
        sa.Column("normalized_description", sa.String(length=255), nullable=True),
        sa.Column("pack_size", sa.String(length=100), nullable=True),
        sa.Column("default_cost", sa.Float(), nullable=True),
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
    )
    op.create_unique_constraint(
        "uq_vendor_item_alias_sku",
        "vendor_item_alias",
        ["vendor_id", "vendor_sku"],
    )
    op.create_unique_constraint(
        "uq_vendor_item_alias_description",
        "vendor_item_alias",
        ["vendor_id", "normalized_description"],
    )
    op.create_index(
        "ix_vendor_item_alias_vendor",
        "vendor_item_alias",
        ["vendor_id"],
    )


def downgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not inspector.has_table("vendor_item_alias"):
        return

    op.drop_index("ix_vendor_item_alias_vendor", table_name="vendor_item_alias")
    op.drop_constraint(
        "uq_vendor_item_alias_description",
        "vendor_item_alias",
        type_="unique",
    )
    op.drop_constraint("uq_vendor_item_alias_sku", "vendor_item_alias", type_="unique")
    op.drop_table("vendor_item_alias")
