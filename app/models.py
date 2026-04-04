import json
from datetime import datetime
from typing import Optional

from flask import current_app, has_app_context
from flask_login import UserMixin
from sqlalchemy import func, select
from sqlalchemy.ext.hybrid import hybrid_property
from sqlalchemy.orm import relationship

from app import db

# Association table for the many-to-many relationship
transfer_items = db.Table(
    "transfer_items",
    db.Column(
        "transfer_id",
        db.Integer,
        db.ForeignKey("transfer.id"),
        primary_key=True,
    ),
    db.Column(
        "item_id", db.Integer, db.ForeignKey("item.id"), primary_key=True
    ),
    db.Column("quantity", db.Integer, nullable=False),
)

# Association table for products available at a location
location_products = db.Table(
    "location_products",
    db.Column(
        "location_id",
        db.Integer,
        db.ForeignKey("location.id"),
        primary_key=True,
    ),
    db.Column(
        "product_id", db.Integer, db.ForeignKey("product.id"), primary_key=True
    ),
)

menu_products = db.Table(
    "menu_products",
    db.Column("menu_id", db.Integer, db.ForeignKey("menu.id"), primary_key=True),
    db.Column(
        "product_id", db.Integer, db.ForeignKey("product.id"), primary_key=True
    ),
)

user_permission_groups = db.Table(
    "user_permission_groups",
    db.Column(
        "user_id",
        db.Integer,
        db.ForeignKey("user.id"),
        primary_key=True,
    ),
    db.Column(
        "permission_group_id",
        db.Integer,
        db.ForeignKey("permission_group.id"),
        primary_key=True,
    ),
)

permission_group_permissions = db.Table(
    "permission_group_permissions",
    db.Column(
        "permission_group_id",
        db.Integer,
        db.ForeignKey("permission_group.id"),
        primary_key=True,
    ),
    db.Column(
        "permission_id",
        db.Integer,
        db.ForeignKey("permission.id"),
        primary_key=True,
    ),
)


class Permission(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(100), unique=True, nullable=False)
    category = db.Column(db.String(50), nullable=False)
    label = db.Column(db.String(100), nullable=False)
    description = db.Column(db.Text, nullable=False, default="")

    groups = relationship(
        "PermissionGroup",
        secondary=permission_group_permissions,
        back_populates="permissions",
    )

    __table_args__ = (db.Index("ix_permission_category_code", "category", "code"),)


class PermissionGroup(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    key = db.Column(db.String(64), unique=True, nullable=True)
    name = db.Column(db.String(100), unique=True, nullable=False)
    description = db.Column(db.Text, nullable=True)
    is_system = db.Column(
        db.Boolean, default=False, nullable=False, server_default="0"
    )

    users = relationship(
        "User",
        secondary=user_permission_groups,
        back_populates="permission_groups",
    )
    permissions = relationship(
        "Permission",
        secondary=permission_group_permissions,
        back_populates="groups",
        order_by="Permission.category, Permission.code",
    )

    __table_args__ = (
        db.Index("ix_permission_group_is_system", "is_system"),
    )


class LocationStandItem(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    location_id = db.Column(
        db.Integer, db.ForeignKey("location.id"), nullable=False
    )
    item_id = db.Column(db.Integer, db.ForeignKey("item.id"), nullable=False)
    expected_count = db.Column(
        db.Float, nullable=False, default=0.0, server_default="0.0"
    )
    purchase_gl_code_id = db.Column(
        db.Integer, db.ForeignKey("gl_code.id"), nullable=True
    )

    purchase_gl_code = relationship(
        "GLCode", foreign_keys=[purchase_gl_code_id]
    )

    location = relationship("Location", back_populates="stand_items")
    item = relationship("Item")

    __table_args__ = (
        db.UniqueConstraint("location_id", "item_id", name="_loc_item_uc"),
    )


class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password = db.Column(db.String(255), nullable=False)
    is_admin = db.Column(db.Boolean, default=False, nullable=False)
    transfers = db.relationship("Transfer", backref="creator", lazy=True)
    invoices = db.relationship("Invoice", backref="creator", lazy=True)
    active = db.Column(db.Boolean, default=False, nullable=False)
    favorites = db.Column(db.Text, default="")
    timezone = db.Column(db.String(50))
    phone_number = db.Column(db.String(20))
    notify_transfers = db.Column(db.Boolean, default=False, nullable=False)
    items_per_page = db.Column(
        db.Integer, nullable=False, default=20, server_default="20"
    )
    last_login_at = db.Column(db.DateTime, nullable=True)
    last_active_at = db.Column(db.DateTime, nullable=True)
    last_forced_login_at = db.Column(db.DateTime, nullable=True)
    filter_preferences = relationship(
        "UserFilterPreference",
        back_populates="user",
        cascade="all, delete-orphan",
    )
    permission_groups = relationship(
        "PermissionGroup",
        secondary=user_permission_groups,
        back_populates="users",
        order_by="PermissionGroup.name",
    )

    @property
    def is_super_admin(self) -> bool:
        return bool(self.is_admin)

    def get_permission_codes(self) -> set[str]:
        if self.is_super_admin:
            return set()
        cached_codes = getattr(self, "_permission_code_cache", None)
        if cached_codes is not None:
            return cached_codes
        codes = {
            permission.code
            for group in self.permission_groups
            for permission in group.permissions
        }
        self._permission_code_cache = codes
        return codes

    def invalidate_permission_cache(self) -> None:
        if hasattr(self, "_permission_code_cache"):
            delattr(self, "_permission_code_cache")

    def has_permission(self, code: str) -> bool:
        if self.is_super_admin:
            return True
        if not code:
            return False
        return code in self.get_permission_codes()

    def has_any_permission(self, *codes: str) -> bool:
        return any(self.has_permission(code) for code in codes if code)

    def has_all_permissions(self, *codes: str) -> bool:
        valid_codes = [code for code in codes if code]
        return all(self.has_permission(code) for code in valid_codes)

    def can_access_endpoint(self, endpoint: str | None, method: str = "GET") -> bool:
        from app.permissions import user_can_access_endpoint

        return user_can_access_endpoint(self, endpoint, method)

    def get_favorites(self):
        """Return the user's favourite endpoint names as a list."""
        favorites = [f for f in (self.favorites or "").split(",") if f]
        if not has_app_context():
            return favorites
        from app.permissions import user_can_access_endpoint

        valid_endpoints = {rule.endpoint for rule in current_app.url_map.iter_rules()}
        return [
            favorite
            for favorite in favorites
            if favorite in valid_endpoints
            and user_can_access_endpoint(self, favorite, "GET")
        ]

    def toggle_favorite(self, endpoint: str):
        """Add or remove an endpoint from the favourites list."""
        favs = set(self.get_favorites())
        if not has_app_context():
            valid_endpoints = set()
        else:
            valid_endpoints = {rule.endpoint for rule in current_app.url_map.iter_rules()}
        favs = {favorite for favorite in favs if favorite in valid_endpoints}
        if endpoint in favs:
            favs.remove(endpoint)
        elif endpoint in valid_endpoints and self.can_access_endpoint(endpoint, "GET"):
            favs.add(endpoint)
        self.favorites = ",".join(sorted(favs))


class UserFilterPreference(db.Model):
    __tablename__ = "user_filter_preference"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    scope = db.Column(db.String(255), nullable=False)
    values = db.Column(db.JSON, nullable=False, default=dict)

    user = relationship("User", back_populates="filter_preferences")

    __table_args__ = (
        db.UniqueConstraint(
            "user_id", "scope", name="uq_user_filter_preference_scope"
        ),
    )

class Location(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), unique=True, nullable=False)
    archived = db.Column(
        db.Boolean, default=False, nullable=False, server_default="0"
    )
    is_spoilage = db.Column(
        db.Boolean, default=False, nullable=False, server_default="0"
    )
    current_menu_id = db.Column(db.Integer, db.ForeignKey("menu.id"), nullable=True)
    products = db.relationship(
        "Product", secondary=location_products, backref="locations"
    )
    stand_items = db.relationship(
        "LocationStandItem",
        back_populates="location",
        cascade="all, delete-orphan",
    )
    event_locations = db.relationship(
        "EventLocation",
        back_populates="location",
        cascade="all, delete-orphan",
    )
    current_menu = relationship(
        "Menu", back_populates="locations", foreign_keys="Location.current_menu_id"
    )
    menu_assignments = relationship(
        "MenuAssignment",
        back_populates="location",
        order_by="MenuAssignment.assigned_at.desc()",
        cascade="all, delete-orphan",
    )
    terminal_sale_location_aliases = relationship(
        "TerminalSaleLocationAlias",
        back_populates="location",
        cascade="all, delete-orphan",
    )

    __table_args__ = (db.Index("ix_location_archived", "archived"),)


class Item(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    base_unit = db.Column(db.String(20), nullable=False)
    upc = db.Column(db.String(32), unique=True, nullable=True)
    gl_code = db.Column(db.String(10), nullable=True)
    gl_code_id = db.Column(
        db.Integer, db.ForeignKey("gl_code.id"), nullable=True
    )
    quantity = db.Column(
        db.Float, nullable=False, default=0.0, server_default="0.0"
    )
    cost = db.Column(
        db.Float, nullable=False, default=0.0, server_default="0.0"
    )
    container_deposit = db.Column(
        db.Float, nullable=False, default=0.0, server_default="0.0"
    )
    purchase_gl_code_id = db.Column(
        db.Integer, db.ForeignKey("gl_code.id"), nullable=True
    )
    purchase_gl_code = relationship(
        "GLCode", foreign_keys=[purchase_gl_code_id]
    )
    transfers = db.relationship(
        "Transfer",
        secondary=transfer_items,
        backref=db.backref("items", lazy="dynamic"),
    )
    recipe_items = relationship(
        "ProductRecipeItem",
        back_populates="item",
        cascade="all, delete-orphan",
    )
    units = relationship(
        "ItemUnit", back_populates="item", cascade="all, delete-orphan"
    )
    barcode_aliases = relationship(
        "ItemBarcode",
        back_populates="item",
        cascade="all, delete-orphan",
        order_by="ItemBarcode.id",
    )
    gl_code_rel = relationship(
        "GLCode", foreign_keys=[gl_code_id], backref="items"
    )
    archived = db.Column(
        db.Boolean, default=False, nullable=False, server_default="0"
    )

    def purchase_gl_code_for_location(self, location_id: int):
        """Return the purchase GL code for this item at a specific location."""
        lsi = LocationStandItem.query.filter_by(
            location_id=location_id, item_id=self.id
        ).first()
        if lsi and lsi.purchase_gl_code:
            return lsi.purchase_gl_code
        return self.purchase_gl_code

    @staticmethod
    def normalize_barcode(value: Optional[str]) -> str:
        return (value or "").strip()

    @property
    def barcode_values(self) -> list[str]:
        values: list[str] = []
        primary = self.normalize_barcode(self.upc)
        if primary:
            values.append(primary)
        for alias in self.barcode_aliases:
            code = self.normalize_barcode(alias.code)
            if code and code not in values:
                values.append(code)
        return values

    @classmethod
    def lookup_by_barcode(cls, value: Optional[str]):
        normalized = cls.normalize_barcode(value)
        if not normalized:
            return None
        item = cls.query.filter_by(upc=normalized).first()
        if item is not None:
            return item
        alias = ItemBarcode.query.filter_by(code=normalized).first()
        if alias is None:
            return None
        return alias.item

    __table_args__ = (
        db.Index(
            "uix_item_name_active",
            "name",
            unique=True,
            postgresql_where=db.text("archived = false"),
        ),
        db.Index("ix_item_archived", "archived"),
    )


class ItemBarcode(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    item_id = db.Column(db.Integer, db.ForeignKey("item.id"), nullable=False)
    code = db.Column(db.String(32), nullable=False)

    item = relationship("Item", back_populates="barcode_aliases")

    __table_args__ = (db.Index("ix_item_barcode_code", "code", unique=True),)


class ItemUnit(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    item_id = db.Column(db.Integer, db.ForeignKey("item.id"), nullable=False)
    name = db.Column(db.String(50), nullable=False)
    factor = db.Column(db.Float, nullable=False)
    receiving_default = db.Column(
        db.Boolean, default=False, nullable=False, server_default="0"
    )
    transfer_default = db.Column(
        db.Boolean, default=False, nullable=False, server_default="0"
    )

    item = relationship("Item", back_populates="units")

    vendor_aliases = relationship(
        "VendorItemAlias",
        back_populates="item_unit",
    )


class VendorItemAlias(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    vendor_id = db.Column(db.Integer, db.ForeignKey("vendor.id"), nullable=False)
    item_id = db.Column(db.Integer, db.ForeignKey("item.id"), nullable=False)
    item_unit_id = db.Column(
        db.Integer, db.ForeignKey("item_unit.id"), nullable=True
    )
    vendor_sku = db.Column(db.String(100), nullable=True)
    vendor_description = db.Column(db.String(255), nullable=True)
    normalized_description = db.Column(db.String(255), nullable=True)
    pack_size = db.Column(db.String(100), nullable=True)
    default_cost = db.Column(db.Float, nullable=True)
    created_at = db.Column(
        db.DateTime, nullable=False, default=datetime.utcnow, server_default=func.now()
    )
    updated_at = db.Column(
        db.DateTime,
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        server_default=func.now(),
    )

    vendor = relationship("Vendor", back_populates="vendor_item_aliases")
    item = relationship("Item", backref="vendor_aliases")
    item_unit = relationship("ItemUnit", back_populates="vendor_aliases")

    __table_args__ = (
        db.UniqueConstraint(
            "vendor_id", "vendor_sku", name="uq_vendor_item_alias_sku"
        ),
        db.UniqueConstraint(
            "vendor_id",
            "normalized_description",
            name="uq_vendor_item_alias_description",
        ),
        db.Index("ix_vendor_item_alias_vendor", "vendor_id"),
    )


class Transfer(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    from_location_id = db.Column(
        db.Integer, db.ForeignKey("location.id"), nullable=False
    )
    to_location_id = db.Column(
        db.Integer, db.ForeignKey("location.id"), nullable=False
    )
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    date_created = db.Column(
        db.DateTime, nullable=False, default=datetime.utcnow
    )
    completed = db.Column(db.Boolean, default=False, nullable=False)
    from_location_name = db.Column(
        db.String(100), nullable=False, server_default=""
    )
    to_location_name = db.Column(
        db.String(100), nullable=False, server_default=""
    )

    # Define relationships to Location model
    from_location = relationship("Location", foreign_keys=[from_location_id])
    to_location = relationship("Location", foreign_keys=[to_location_id])
    transfer_items = db.relationship(
        "TransferItem", backref="transfer", cascade="all, delete-orphan"
    )

    __table_args__ = (
        db.Index(
            "ix_transfer_to_location_completed",
            "to_location_id",
            "completed",
        ),
        db.Index("ix_transfer_date_created", "date_created"),
        db.Index("ix_transfer_user_id", "user_id"),
    )


class TransferItem(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    transfer_id = db.Column(
        db.Integer, db.ForeignKey("transfer.id"), nullable=False
    )
    item_id = db.Column(db.Integer, db.ForeignKey("item.id"), nullable=False)
    quantity = db.Column(db.Float, nullable=False)
    completed_quantity = db.Column(
        db.Float, nullable=False, default=0.0, server_default="0.0"
    )
    completed_at = db.Column(db.DateTime, nullable=True)
    completed_by_id = db.Column(
        db.Integer, db.ForeignKey("user.id"), nullable=True
    )
    unit_id = db.Column(db.Integer, db.ForeignKey("item_unit.id"), nullable=True)
    unit_quantity = db.Column(db.Float, nullable=True)
    base_quantity = db.Column(db.Float, nullable=True)
    item = relationship("Item", backref="transfer_items", lazy=True)
    unit = relationship("ItemUnit", foreign_keys=[unit_id])
    completed_by = relationship("User", foreign_keys=[completed_by_id])
    item_name = db.Column(db.String(100), nullable=False, server_default="")


class Customer(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    first_name = db.Column(db.String(50), nullable=False)
    last_name = db.Column(db.String(50), nullable=False)
    gst_exempt = db.Column(
        db.Boolean, default=False, nullable=False, server_default="0"
    )
    pst_exempt = db.Column(
        db.Boolean, default=False, nullable=False, server_default="0"
    )
    archived = db.Column(
        db.Boolean, default=False, nullable=False, server_default="0"
    )

    invoices = db.relationship("Invoice", backref="customer", lazy=True)

    __table_args__ = (db.Index("ix_customer_archived", "archived"),)


class Vendor(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    first_name = db.Column(db.String(50), nullable=False)
    last_name = db.Column(db.String(50), nullable=False)
    gst_exempt = db.Column(
        db.Boolean, default=False, nullable=False, server_default="0"
    )
    pst_exempt = db.Column(
        db.Boolean, default=False, nullable=False, server_default="0"
    )
    archived = db.Column(
        db.Boolean, default=False, nullable=False, server_default="0"
    )

    vendor_item_aliases = relationship(
        "VendorItemAlias",
        back_populates="vendor",
        cascade="all, delete-orphan",
    )

    __table_args__ = (db.Index("ix_vendor_archived", "archived"),)


class GLCode(db.Model):
    __tablename__ = "gl_code"
    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(6), unique=True, nullable=False)
    description = db.Column(db.String(255))


class Product(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    gl_code = db.Column(db.String(10), nullable=True)
    # Terminal/event sale price retained for POS and stand-sheet workflows.
    price = db.Column(db.Float, nullable=False)
    # Dedicated unit price used when creating customer invoices.
    invoice_sale_price = db.Column(
        db.Numeric(10, 2),
        nullable=True,
    )
    cost = db.Column(
        db.Float, nullable=False, default=0.0, server_default="0.0"
    )
    gl_code_id = db.Column(
        db.Integer, db.ForeignKey("gl_code.id"), nullable=True
    )
    quantity = db.Column(
        db.Float, nullable=False, default=0.0, server_default="0.0"
    )
    recipe_yield_quantity = db.Column(
        db.Float, nullable=False, default=1.0, server_default="1.0"
    )
    recipe_yield_unit = db.Column(db.String(50), nullable=True)
    sales_gl_code_id = db.Column(
        db.Integer, db.ForeignKey("gl_code.id"), nullable=True
    )
    sales_gl_code = relationship("GLCode", foreign_keys=[sales_gl_code_id])

    # Define a one-to-many relationship with InvoiceProduct
    invoice_products = relationship("InvoiceProduct", back_populates="product")
    recipe_items = relationship(
        "ProductRecipeItem",
        back_populates="product",
        cascade="all, delete-orphan",
    )
    gl_code_rel = relationship(
        "GLCode", foreign_keys=[gl_code_id], backref="products"
    )
    terminal_sales = relationship(
        "TerminalSale", back_populates="product", cascade="all, delete-orphan"
    )
    terminal_sale_aliases = relationship(
        "TerminalSaleProductAlias",
        back_populates="product",
        cascade="all, delete-orphan",
    )
    menus = relationship(
        "Menu", secondary=menu_products, back_populates="products"
    )

    @hybrid_property
    def last_sold_at(self):
        """Return the most recent sale date from invoices or terminal sales."""
        dates = [
            ip.invoice.date_created
            for ip in self.invoice_products
            if ip.invoice and ip.invoice.date_created
        ]
        dates.extend(ts.sold_at for ts in self.terminal_sales if ts.sold_at)
        return max(dates) if dates else None


    @property
    def food_cost_percentage(self) -> float:
        """Return the food cost as a percentage of the price before tax."""
        if self.price:
            return (self.cost / self.price) * 100
        return 0.0


class Menu(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False, unique=True)
    description = db.Column(db.Text, nullable=True)
    created_at = db.Column(
        db.DateTime, nullable=False, default=datetime.utcnow, server_default=func.now()
    )
    updated_at = db.Column(
        db.DateTime,
        nullable=False,
        default=datetime.utcnow,
        server_default=func.now(),
        onupdate=datetime.utcnow,
    )
    last_used_at = db.Column(db.DateTime, nullable=True)

    products = relationship(
        "Product", secondary=menu_products, back_populates="menus"
    )
    assignments = relationship(
        "MenuAssignment",
        back_populates="menu",
        order_by="MenuAssignment.assigned_at.desc()",
        cascade="all, delete-orphan",
    )
    locations = relationship(
        "Location", back_populates="current_menu", foreign_keys="Location.current_menu_id"
    )


class MenuAssignment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    menu_id = db.Column(db.Integer, db.ForeignKey("menu.id"), nullable=False)
    location_id = db.Column(db.Integer, db.ForeignKey("location.id"), nullable=False)
    assigned_at = db.Column(
        db.DateTime, nullable=False, default=datetime.utcnow, server_default=func.now()
    )
    unassigned_at = db.Column(db.DateTime, nullable=True)

    menu = relationship("Menu", back_populates="assignments")
    location = relationship("Location", back_populates="menu_assignments")

    __table_args__ = (
        db.Index("ix_menu_assignment_active", "location_id", "unassigned_at"),
    )


class Invoice(db.Model):
    STATUS_PENDING = "pending"
    STATUS_DELIVERED = "delivered"
    STATUS_PAID = "paid"

    id = db.Column(
        db.String(32), primary_key=True
    )  # Adjust length based on your requirements
    user_id = db.Column(
        db.Integer, db.ForeignKey("user.id"), nullable=False
    )  # Reference to the user who created the invoice
    customer_id = db.Column(
        db.Integer, db.ForeignKey("customer.id"), nullable=False, index=True
    )
    date_created = db.Column(
        db.DateTime, nullable=False, default=datetime.utcnow, index=True
    )
    status = db.Column(
        db.String(32),
        nullable=False,
        default=STATUS_PENDING,
        server_default=STATUS_PENDING,
    )
    delivered_at = db.Column(db.DateTime, nullable=True)
    is_paid = db.Column(
        db.Boolean, nullable=False, default=False, server_default="0"
    )
    paid_at = db.Column(db.DateTime, nullable=True)

    __table_args__ = (
        db.CheckConstraint(
            "status IN ('pending', 'delivered', 'paid')",
            name="ck_invoice_status",
        ),
        db.Index("ix_invoice_status", "status"),
        db.Index("ix_invoice_user_id", "user_id"),
    )

    # Define the relationship with InvoiceProduct, specifying the foreign_keys argument
    products = db.relationship(
        "InvoiceProduct",
        backref="invoice",
        lazy=True,
        foreign_keys="[InvoiceProduct.invoice_id]",
        cascade="all, delete-orphan",
    )

    @property
    def total(self):
        return sum(
            p.line_subtotal + p.line_gst + p.line_pst for p in self.products
        )

    @property
    def invoice_status(self) -> str:
        if self.status == self.STATUS_PAID or self.is_paid:
            return self.STATUS_PAID
        if self.status == self.STATUS_DELIVERED:
            return self.STATUS_DELIVERED
        return self.STATUS_PENDING

    @property
    def invoice_status_label(self) -> str:
        return self.invoice_status.title()

    @property
    def invoice_status_badge_class(self) -> str:
        return {
            self.STATUS_PENDING: "text-bg-warning",
            self.STATUS_DELIVERED: "text-bg-info",
            self.STATUS_PAID: "text-bg-success",
        }.get(self.invoice_status, "text-bg-warning")

    @property
    def payment_status_label(self) -> str:
        return "Paid" if self.is_paid else "Unpaid"

    @property
    def can_mark_delivered(self) -> bool:
        return self.invoice_status == self.STATUS_PENDING

    @property
    def can_mark_paid(self) -> bool:
        return self.invoice_status == self.STATUS_DELIVERED


class InvoiceProduct(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    invoice_id = db.Column(
        db.String(32),
        db.ForeignKey("invoice.id", ondelete="CASCADE"),
        nullable=False,
    )
    quantity = db.Column(db.Float, nullable=False)
    product_id = db.Column(
        db.Integer,
        db.ForeignKey("product.id", ondelete="SET NULL"),
        nullable=True,
    )
    product = relationship("Product", back_populates="invoice_products")
    is_custom_line = db.Column(
        db.Boolean, nullable=False, default=False, server_default="0"
    )
    product_name = db.Column(db.String(100), nullable=False)
    unit_price = db.Column(db.Float, nullable=False)
    line_subtotal = db.Column(db.Float, nullable=False)
    line_gst = db.Column(db.Float, nullable=False)
    line_pst = db.Column(db.Float, nullable=False)

    # New tax override fields
    override_gst = db.Column(
        db.Boolean, nullable=True
    )  # True = apply GST, False = exempt, None = fallback to customer
    override_pst = db.Column(
        db.Boolean, nullable=True
    )  # True = apply PST, False = exempt, None = fallback to customer


class ProductRecipeItem(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    product_id = db.Column(
        db.Integer, db.ForeignKey("product.id"), nullable=False
    )
    item_id = db.Column(db.Integer, db.ForeignKey("item.id"), nullable=False)
    unit_id = db.Column(
        db.Integer, db.ForeignKey("item_unit.id"), nullable=True
    )
    quantity = db.Column(db.Float, nullable=False)
    countable = db.Column(
        db.Boolean, default=False, nullable=False, server_default="0"
    )

    product = relationship("Product", back_populates="recipe_items")
    item = relationship("Item", back_populates="recipe_items")
    unit = relationship("ItemUnit")


class PurchaseOrder(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    vendor_id = db.Column(
        db.Integer, db.ForeignKey("vendor.id"), nullable=False
    )
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    vendor_name = db.Column(db.String(100), nullable=False, server_default="")
    order_number = db.Column(db.String(100), nullable=True)
    order_date = db.Column(db.Date, nullable=False)
    expected_date = db.Column(db.Date, nullable=False)
    expected_total_cost = db.Column(db.Float, nullable=True)
    delivery_charge = db.Column(db.Float, nullable=False, default=0.0)
    received = db.Column(db.Boolean, default=False, nullable=False)
    items = relationship(
        "PurchaseOrderItem",
        backref="purchase_order",
        cascade="all, delete-orphan",
        order_by="PurchaseOrderItem.position",
    )
    vendor = relationship("Vendor", backref="purchase_orders")


class PurchaseOrderItem(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    purchase_order_id = db.Column(
        db.Integer, db.ForeignKey("purchase_order.id"), nullable=False
    )
    position = db.Column(
        db.Integer, nullable=False, default=0, server_default="0"
    )
    product_id = db.Column(
        db.Integer, db.ForeignKey("product.id"), nullable=True
    )
    unit_id = db.Column(
        db.Integer, db.ForeignKey("item_unit.id"), nullable=True
    )
    item_id = db.Column(db.Integer, db.ForeignKey("item.id"), nullable=True)
    quantity = db.Column(db.Float, nullable=False)
    unit_cost = db.Column(db.Float, nullable=True)
    product = relationship("Product")
    unit = relationship("ItemUnit")
    item = relationship("Item")


class PurchaseInvoice(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    purchase_order_id = db.Column(
        db.Integer, db.ForeignKey("purchase_order.id"), nullable=False
    )
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    location_id = db.Column(
        db.Integer, db.ForeignKey("location.id"), nullable=False
    )
    vendor_name = db.Column(db.String(100), nullable=False, server_default="")
    location_name = db.Column(
        db.String(100), nullable=False, server_default=""
    )
    received_date = db.Column(db.Date, nullable=False)
    invoice_number = db.Column(db.String(50), nullable=True)
    department = db.Column(db.String(50), nullable=True)
    gst = db.Column(db.Float, nullable=False, default=0.0)
    pst = db.Column(db.Float, nullable=False, default=0.0)
    delivery_charge = db.Column(db.Float, nullable=False, default=0.0)
    items = relationship(
        "PurchaseInvoiceItem",
        backref="invoice",
        cascade="all, delete-orphan",
        order_by="PurchaseInvoiceItem.position",
    )
    location = relationship("Location")
    purchase_order = relationship("PurchaseOrder")

    @property
    def item_total(self):
        return sum(i.quantity * (i.cost + i.container_deposit) for i in self.items)

    @property
    def total(self):
        return self.item_total + self.delivery_charge + self.gst + self.pst


class PurchaseInvoiceItem(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    invoice_id = db.Column(
        db.Integer, db.ForeignKey("purchase_invoice.id"), nullable=False
    )
    position = db.Column(
        db.Integer, nullable=False, default=0, server_default="0"
    )
    item_id = db.Column(
        db.Integer,
        db.ForeignKey("item.id", ondelete="SET NULL"),
        nullable=True,
    )
    unit_id = db.Column(
        db.Integer,
        db.ForeignKey("item_unit.id", ondelete="SET NULL"),
        nullable=True,
    )
    item_name = db.Column(db.String(100), nullable=False)
    unit_name = db.Column(db.String(50), nullable=True)
    quantity = db.Column(db.Float, nullable=False)
    cost = db.Column(db.Float, nullable=False)
    container_deposit = db.Column(
        db.Float, nullable=False, default=0.0, server_default="0.0"
    )
    prev_cost = db.Column(db.Float, nullable=False, default=0.0)
    item = relationship("Item")
    unit = relationship("ItemUnit")
    location_id = db.Column(
        db.Integer,
        db.ForeignKey("location.id", ondelete="SET NULL"),
        nullable=True,
    )
    location = relationship("Location")
    purchase_gl_code_id = db.Column(
        db.Integer,
        db.ForeignKey("gl_code.id"),
        nullable=True,
    )
    purchase_gl_code = relationship(
        "GLCode", foreign_keys=[purchase_gl_code_id]
    )

    @property
    def line_total(self):
        return self.quantity * (abs(self.cost) + abs(self.container_deposit))

    def resolved_purchase_gl_code(self, location_id: Optional[int] = None):
        """Return the effective purchase GL code for this invoice line."""
        if self.purchase_gl_code:
            return self.purchase_gl_code

        if not self.item:
            return None

        loc_id = self.location_id if self.location_id is not None else location_id
        if loc_id is None and self.invoice is not None:
            loc_id = self.invoice.location_id

        if loc_id is not None:
            return self.item.purchase_gl_code_for_location(loc_id)

        return self.item.purchase_gl_code


class PurchaseInvoiceDraft(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    purchase_order_id = db.Column(
        db.Integer, db.ForeignKey("purchase_order.id"), nullable=False, unique=True
    )
    payload = db.Column(db.Text, nullable=False)
    created_at = db.Column(
        db.DateTime, nullable=False, default=datetime.utcnow, server_default=func.now()
    )
    updated_at = db.Column(
        db.DateTime,
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        server_default=func.now(),
    )

    purchase_order = relationship("PurchaseOrder")

    @property
    def data(self):
        try:
            return json.loads(self.payload)
        except (TypeError, ValueError):
            return {}

    def update_payload(self, data: dict):
        self.payload = json.dumps(data)


class PurchaseOrderItemArchive(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    purchase_order_id = db.Column(db.Integer, nullable=False)
    position = db.Column(
        db.Integer, nullable=False, default=0, server_default="0"
    )
    item_id = db.Column(db.Integer, nullable=False)
    unit_id = db.Column(db.Integer, nullable=True)
    quantity = db.Column(db.Float, nullable=False)
    unit_cost = db.Column(db.Float, nullable=True)
    archived_at = db.Column(
        db.DateTime, default=datetime.utcnow, nullable=False
    )


class ActivityLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)
    activity = db.Column(db.Text, nullable=False)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    user = relationship("User", backref="activity_logs")


class Note(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    entity_type = db.Column(db.String(50), nullable=False)
    entity_id = db.Column(db.String(64), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    content = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(
        db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )
    pinned = db.Column(
        db.Boolean, default=False, nullable=False, server_default="0"
    )
    pinned_at = db.Column(db.DateTime, nullable=True)

    user = relationship("User", backref="notes")

    __table_args__ = (
        db.Index("ix_note_entity", "entity_type", "entity_id"),
        db.Index("ix_note_pinned", "entity_type", "pinned"),
    )

    def set_pinned(self, value: bool) -> None:
        """Update the pinned state and timestamp."""

        if value and not self.pinned:
            self.pinned = True
            self.pinned_at = datetime.utcnow()
        elif not value and self.pinned:
            self.pinned = False
            self.pinned_at = None


class Event(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    start_date = db.Column(db.Date, nullable=False)
    end_date = db.Column(db.Date, nullable=False)
    closed = db.Column(
        db.Boolean, default=False, nullable=False, server_default="0"
    )
    event_type = db.Column(
        db.String(20), nullable=False, default="other", server_default="other"
    )
    estimated_sales = db.Column(db.Numeric(12, 2), nullable=True)

    locations = relationship(
        "EventLocation", back_populates="event", cascade="all, delete-orphan"
    )


class EventLocation(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    event_id = db.Column(db.Integer, db.ForeignKey("event.id"), nullable=False)
    location_id = db.Column(
        db.Integer, db.ForeignKey("location.id"), nullable=False
    )
    opening_count = db.Column(
        db.Float, nullable=False, default=0.0, server_default="0.0"
    )
    closing_count = db.Column(
        db.Float, nullable=False, default=0.0, server_default="0.0"
    )
    confirmed = db.Column(
        db.Boolean, nullable=False, default=False, server_default="0"
    )
    notes = db.Column(db.Text, nullable=True)

    event = relationship("Event", back_populates="locations")
    location = relationship("Location", back_populates="event_locations")
    terminal_sales = relationship(
        "TerminalSale",
        back_populates="event_location",
        cascade="all, delete-orphan",
    )
    terminal_sales_summary = relationship(
        "EventLocationTerminalSalesSummary",
        back_populates="event_location",
        cascade="all, delete-orphan",
        uselist=False,
    )
    stand_sheet_items = relationship(
        "EventStandSheetItem",
        back_populates="event_location",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        db.UniqueConstraint("event_id", "location_id", name="_event_loc_uc"),
    )


class TerminalSale(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    event_location_id = db.Column(
        db.Integer, db.ForeignKey("event_location.id"), nullable=False
    )
    product_id = db.Column(
        db.Integer, db.ForeignKey("product.id"), nullable=False
    )
    quantity = db.Column(db.Float, nullable=False)
    sold_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    event_location = relationship(
        "EventLocation", back_populates="terminal_sales"
    )
    product = relationship("Product", back_populates="terminal_sales")


class EventLocationTerminalSalesSummary(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    event_location_id = db.Column(
        db.Integer,
        db.ForeignKey("event_location.id"),
        nullable=False,
        unique=True,
    )
    source_location = db.Column(db.String(255), nullable=True)
    total_quantity = db.Column(db.Float, nullable=True)
    total_amount = db.Column(db.Float, nullable=True)
    variance_details = db.Column(db.JSON, nullable=True)
    updated_at = db.Column(
        db.DateTime,
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )

    event_location = relationship(
        "EventLocation", back_populates="terminal_sales_summary"
    )


class TerminalSaleProductAlias(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    source_name = db.Column(db.String(255), nullable=False)
    normalized_name = db.Column(
        db.String(255), nullable=False, unique=True
    )
    product_id = db.Column(
        db.Integer, db.ForeignKey("product.id"), nullable=False
    )
    created_at = db.Column(
        db.DateTime, nullable=False, default=datetime.utcnow
    )

    product = relationship("Product", back_populates="terminal_sale_aliases")


class TerminalSaleLocationAlias(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    source_name = db.Column(db.String(255), nullable=False)
    normalized_name = db.Column(
        db.String(255), nullable=False, unique=True
    )
    location_id = db.Column(
        db.Integer, db.ForeignKey("location.id"), nullable=False
    )
    created_at = db.Column(
        db.DateTime, nullable=False, default=datetime.utcnow
    )

    location = relationship(
        "Location", back_populates="terminal_sale_location_aliases"
    )


class TerminalSalesResolutionState(db.Model):
    __tablename__ = "terminal_sales_resolution_state"

    id = db.Column(db.Integer, primary_key=True)
    event_id = db.Column(db.Integer, db.ForeignKey("event.id"), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    token_id = db.Column(db.String(128), nullable=False)
    payload = db.Column(db.JSON, nullable=False)
    created_at = db.Column(
        db.DateTime,
        nullable=False,
        default=datetime.utcnow,
        server_default=func.now(),
    )
    updated_at = db.Column(
        db.DateTime,
        nullable=False,
        default=datetime.utcnow,
        server_default=func.now(),
        onupdate=datetime.utcnow,
    )

    event = relationship("Event")
    user = relationship("User")

    __table_args__ = (
        db.UniqueConstraint(
            "event_id",
            "user_id",
            "token_id",
            name="uq_terminal_sales_state_event_user_token",
        ),
        db.Index(
            "ix_terminal_sales_state_event_user",
            "event_id",
            "user_id",
        ),
    )


class PosSalesImport(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    source_provider = db.Column(db.String(100), nullable=False)
    message_id = db.Column(db.String(255), nullable=False)
    attachment_filename = db.Column(db.String(255), nullable=False)
    attachment_sha256 = db.Column(db.String(64), nullable=False)
    attachment_storage_path = db.Column(db.String(1024), nullable=True)
    received_at = db.Column(
        db.DateTime,
        nullable=False,
        default=datetime.utcnow,
        server_default=func.now(),
    )
    status = db.Column(
        db.String(32),
        nullable=False,
        default="pending",
        server_default="pending",
    )
    approved_by = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)
    approved_at = db.Column(db.DateTime, nullable=True)
    reversed_by = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)
    reversed_at = db.Column(db.DateTime, nullable=True)
    reversal_reason = db.Column(db.Text, nullable=True)
    deleted_by = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)
    deleted_at = db.Column(db.DateTime, nullable=True)
    deletion_reason = db.Column(db.Text, nullable=True)
    approval_batch_id = db.Column(db.String(64), nullable=True)
    reversal_batch_id = db.Column(db.String(64), nullable=True)
    failure_reason = db.Column(db.Text, nullable=True)
    created_at = db.Column(
        db.DateTime,
        nullable=False,
        default=datetime.utcnow,
        server_default=func.now(),
    )
    updated_at = db.Column(
        db.DateTime,
        nullable=False,
        default=datetime.utcnow,
        server_default=func.now(),
        onupdate=datetime.utcnow,
    )

    approver = relationship("User", foreign_keys=[approved_by])
    reverser = relationship("User", foreign_keys=[reversed_by])
    deleter = relationship("User", foreign_keys=[deleted_by])
    locations = relationship(
        "PosSalesImportLocation",
        back_populates="sales_import",
        cascade="all, delete-orphan",
        order_by="PosSalesImportLocation.parse_index.asc()",
    )
    rows = relationship(
        "PosSalesImportRow",
        back_populates="sales_import",
        cascade="all, delete-orphan",
        order_by="PosSalesImportRow.parse_index.asc()",
    )

    __table_args__ = (
        db.UniqueConstraint(
            "source_provider",
            "message_id",
            "attachment_sha256",
            name="uq_pos_sales_import_idempotency",
        ),
        db.CheckConstraint(
            "status IN ('pending', 'needs_mapping', 'approved', 'reversed', 'deleted', 'failed')",
            name="ck_pos_sales_import_status",
        ),
        db.Index("ix_pos_sales_import_status_received_at", "status", "received_at"),
        db.Index("ix_pos_sales_import_received_at", "received_at"),
        db.Index("ix_pos_sales_import_approved_by", "approved_by", "approved_at"),
        db.Index("ix_pos_sales_import_reversed_by", "reversed_by", "reversed_at"),
        db.Index("ix_pos_sales_import_deleted_by", "deleted_by", "deleted_at"),
        db.Index("ix_pos_sales_import_approval_batch", "approval_batch_id"),
        db.Index("ix_pos_sales_import_reversal_batch", "reversal_batch_id"),
    )


class PosSalesImportLocation(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    import_id = db.Column(
        db.Integer,
        db.ForeignKey("pos_sales_import.id", ondelete="CASCADE"),
        nullable=False,
    )
    source_location_name = db.Column(db.String(255), nullable=False)
    normalized_location_name = db.Column(db.String(255), nullable=False)
    location_id = db.Column(db.Integer, db.ForeignKey("location.id"), nullable=True)
    total_quantity = db.Column(db.Float, nullable=False, default=0.0, server_default="0.0")
    net_inc = db.Column(db.Float, nullable=False, default=0.0, server_default="0.0")
    discounts_abs = db.Column(db.Float, nullable=False, default=0.0, server_default="0.0")
    computed_total = db.Column(db.Float, nullable=False, default=0.0, server_default="0.0")
    parse_index = db.Column(db.Integer, nullable=False)
    approval_batch_id = db.Column(db.String(64), nullable=True)
    reversal_batch_id = db.Column(db.String(64), nullable=True)
    created_at = db.Column(
        db.DateTime,
        nullable=False,
        default=datetime.utcnow,
        server_default=func.now(),
    )
    updated_at = db.Column(
        db.DateTime,
        nullable=False,
        default=datetime.utcnow,
        server_default=func.now(),
        onupdate=datetime.utcnow,
    )

    sales_import = relationship("PosSalesImport", back_populates="locations")
    location = relationship("Location")
    rows = relationship(
        "PosSalesImportRow",
        back_populates="import_location",
        cascade="all, delete-orphan",
        order_by="PosSalesImportRow.parse_index.asc()",
    )

    __table_args__ = (
        db.UniqueConstraint("import_id", "parse_index", name="uq_pos_sales_import_location_order"),
        db.Index("ix_pos_sales_import_location_import", "import_id"),
        db.Index("ix_pos_sales_import_location_normalized", "normalized_location_name"),
        db.Index("ix_pos_sales_import_location_location_id", "location_id"),
        db.Index("ix_pos_sales_import_location_approval_batch", "approval_batch_id"),
        db.Index("ix_pos_sales_import_location_reversal_batch", "reversal_batch_id"),
    )


class PosSalesImportRow(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    import_id = db.Column(
        db.Integer,
        db.ForeignKey("pos_sales_import.id", ondelete="CASCADE"),
        nullable=False,
    )
    location_import_id = db.Column(
        db.Integer,
        db.ForeignKey("pos_sales_import_location.id", ondelete="CASCADE"),
        nullable=False,
    )
    source_product_code = db.Column(db.String(128), nullable=True)
    source_product_name = db.Column(db.String(255), nullable=False)
    normalized_product_name = db.Column(db.String(255), nullable=False)
    product_id = db.Column(db.Integer, db.ForeignKey("product.id"), nullable=True)
    quantity = db.Column(db.Float, nullable=False, default=0.0, server_default="0.0")
    net_inc = db.Column(db.Float, nullable=False, default=0.0, server_default="0.0")
    discount_raw = db.Column(db.String(64), nullable=True)
    discount_abs = db.Column(db.Float, nullable=False, default=0.0, server_default="0.0")
    computed_line_total = db.Column(
        db.Float, nullable=False, default=0.0, server_default="0.0"
    )
    computed_unit_price = db.Column(
        db.Float, nullable=False, default=0.0, server_default="0.0"
    )
    parse_index = db.Column(db.Integer, nullable=False)
    is_zero_quantity = db.Column(
        db.Boolean, nullable=False, default=False, server_default="0"
    )
    approval_batch_id = db.Column(db.String(64), nullable=True)
    reversal_batch_id = db.Column(db.String(64), nullable=True)
    approval_metadata = db.Column(db.Text, nullable=True)
    created_at = db.Column(
        db.DateTime,
        nullable=False,
        default=datetime.utcnow,
        server_default=func.now(),
    )
    updated_at = db.Column(
        db.DateTime,
        nullable=False,
        default=datetime.utcnow,
        server_default=func.now(),
        onupdate=datetime.utcnow,
    )

    sales_import = relationship("PosSalesImport", back_populates="rows")
    import_location = relationship("PosSalesImportLocation", back_populates="rows")
    product = relationship("Product")

    __table_args__ = (
        db.UniqueConstraint(
            "location_import_id", "parse_index", name="uq_pos_sales_import_row_order"
        ),
        db.Index("ix_pos_sales_import_row_import", "import_id"),
        db.Index("ix_pos_sales_import_row_location_import", "location_import_id"),
        db.Index("ix_pos_sales_import_row_normalized_product", "normalized_product_name"),
        db.Index("ix_pos_sales_import_row_product_id", "product_id"),
        db.Index("ix_pos_sales_import_row_zero_qty", "is_zero_quantity"),
        db.Index("ix_pos_sales_import_row_approval_batch", "approval_batch_id"),
        db.Index("ix_pos_sales_import_row_reversal_batch", "reversal_batch_id"),
    )


class EventStandSheetItem(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    event_location_id = db.Column(
        db.Integer, db.ForeignKey("event_location.id"), nullable=False
    )
    item_id = db.Column(db.Integer, db.ForeignKey("item.id"), nullable=False)
    opening_count = db.Column(
        db.Float, nullable=False, default=0.0, server_default="0.0"
    )
    transferred_in = db.Column(
        db.Float, nullable=False, default=0.0, server_default="0.0"
    )
    transferred_out = db.Column(
        db.Float, nullable=False, default=0.0, server_default="0.0"
    )
    adjustments = db.Column(
        db.Float, nullable=False, default=0.0, server_default="0.0"
    )
    eaten = db.Column(
        db.Float, nullable=False, default=0.0, server_default="0.0"
    )
    spoiled = db.Column(
        db.Float, nullable=False, default=0.0, server_default="0.0"
    )
    closing_count = db.Column(
        db.Float, nullable=False, default=0.0, server_default="0.0"
    )

    event_location = relationship(
        "EventLocation", back_populates="stand_sheet_items"
    )
    item = relationship("Item")

    __table_args__ = (
        db.UniqueConstraint(
            "event_location_id", "item_id", name="_event_loc_item_uc"
        ),
    )


class Setting(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50), unique=True, nullable=False)
    value = db.Column(db.String(255))

    RECEIVE_LOCATION_SETTING = "PURCHASE_RECEIVE_LOCATION_DEFAULTS"
    PURCHASE_IMPORT_VENDORS = "PURCHASE_IMPORT_VENDORS"
    DEFAULT_PURCHASE_IMPORT_VENDORS = ["SYSCO", "PRATTS", "CENTRAL SUPPLY"]

    @classmethod
    def get_receive_location_defaults(cls) -> dict[str, int]:
        """Return default receiving locations keyed by department."""

        setting = cls.query.filter_by(name=cls.RECEIVE_LOCATION_SETTING).first()
        if setting is None or not setting.value:
            return {}
        try:
            data = json.loads(setting.value)
        except (TypeError, ValueError):
            return {}
        if not isinstance(data, dict):
            return {}
        defaults: dict[str, int] = {}
        for department, location_id in data.items():
            try:
                cast_location_id = int(location_id)
            except (TypeError, ValueError):
                continue
            if cast_location_id:
                defaults[str(department)] = cast_location_id
        return defaults

    @classmethod
    def set_receive_location_defaults(cls, defaults: dict[str, int]):
        """Persist default receiving locations for departments."""

        cleaned = {}
        for department, location_id in defaults.items():
            try:
                cast_location_id = int(location_id)
            except (TypeError, ValueError):
                continue
            if cast_location_id:
                cleaned[str(department)] = cast_location_id

        setting = cls.query.filter_by(name=cls.RECEIVE_LOCATION_SETTING).first()
        if setting is None:
            setting = cls(name=cls.RECEIVE_LOCATION_SETTING)
            db.session.add(setting)
        setting.value = json.dumps(cleaned)
        return setting

    @classmethod
    def get_enabled_purchase_import_vendors(cls) -> list[str]:
        """Return the enabled vendors for purchase-order imports."""

        setting = cls.query.filter_by(name=cls.PURCHASE_IMPORT_VENDORS).first()
        if setting is None or not setting.value:
            return list(cls.DEFAULT_PURCHASE_IMPORT_VENDORS)

        try:
            vendors = json.loads(setting.value)
        except (TypeError, ValueError):
            return list(cls.DEFAULT_PURCHASE_IMPORT_VENDORS)

        cleaned = []
        for vendor in vendors:
            if isinstance(vendor, str) and vendor.strip():
                cleaned.append(vendor.strip())

        return cleaned or list(cls.DEFAULT_PURCHASE_IMPORT_VENDORS)

    @classmethod
    def set_enabled_purchase_import_vendors(cls, vendors: list[str]):
        """Persist enabled vendors for purchase-order imports."""

        cleaned: list[str] = []
        for vendor in vendors:
            if isinstance(vendor, str) and vendor.strip():
                cleaned.append(vendor.strip())

        setting = cls.query.filter_by(name=cls.PURCHASE_IMPORT_VENDORS).first()
        if setting is None:
            setting = cls(name=cls.PURCHASE_IMPORT_VENDORS)
            db.session.add(setting)

        setting.value = json.dumps(cleaned)
        return setting
