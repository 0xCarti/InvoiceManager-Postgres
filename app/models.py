import json
import secrets
from datetime import date as date_cls, datetime, timedelta
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
    display_name = db.Column(db.String(120), nullable=True)
    password = db.Column(db.String(255), nullable=False)
    is_admin = db.Column(db.Boolean, default=False, nullable=False)
    transfers = db.relationship("Transfer", backref="creator", lazy=True)
    invoices = db.relationship("Invoice", backref="creator", lazy=True)
    active = db.Column(db.Boolean, default=False, nullable=False)
    favorites = db.Column(db.Text, default="")
    timezone = db.Column(db.String(50))
    phone_number = db.Column(db.String(20))
    notify_transfers = db.Column(db.Boolean, default=False, nullable=False)
    hourly_rate = db.Column(
        db.Float, nullable=True, default=0.0, server_default="0.0"
    )
    desired_weekly_hours = db.Column(
        db.Float, nullable=True, default=0.0, server_default="0.0"
    )
    max_weekly_hours = db.Column(
        db.Float, nullable=True, default=0.0, server_default="0.0"
    )
    schedule_enabled = db.Column(
        db.Boolean, default=True, nullable=False, server_default="1"
    )
    schedule_notes = db.Column(db.Text, nullable=True)
    notify_schedule_post_email = db.Column(
        db.Boolean, default=True, nullable=False, server_default="1"
    )
    notify_schedule_post_text = db.Column(
        db.Boolean, default=False, nullable=False, server_default="0"
    )
    notify_schedule_changes_email = db.Column(
        db.Boolean, default=True, nullable=False, server_default="1"
    )
    notify_schedule_changes_text = db.Column(
        db.Boolean, default=False, nullable=False, server_default="0"
    )
    notify_tradeboard_email = db.Column(
        db.Boolean, default=True, nullable=False, server_default="1"
    )
    notify_tradeboard_text = db.Column(
        db.Boolean, default=False, nullable=False, server_default="0"
    )
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
    department_memberships = relationship(
        "UserDepartmentMembership",
        back_populates="user",
        foreign_keys="UserDepartmentMembership.user_id",
        cascade="all, delete-orphan",
        order_by="UserDepartmentMembership.department_id.asc()",
    )
    managed_department_memberships = relationship(
        "UserDepartmentMembership",
        back_populates="reports_to",
        foreign_keys="UserDepartmentMembership.reports_to_user_id",
    )
    position_eligibilities = relationship(
        "UserPositionEligibility",
        back_populates="user",
        cascade="all, delete-orphan",
        order_by="UserPositionEligibility.priority.desc()",
    )
    recurring_availability_windows = relationship(
        "RecurringAvailabilityWindow",
        back_populates="user",
        cascade="all, delete-orphan",
        order_by=(
            "RecurringAvailabilityWindow.weekday.asc(), "
            "RecurringAvailabilityWindow.start_time.asc()"
        ),
    )
    availability_overrides = relationship(
        "AvailabilityOverride",
        back_populates="user",
        cascade="all, delete-orphan",
        order_by="AvailabilityOverride.start_at.asc()",
    )
    time_off_requests = relationship(
        "TimeOffRequest",
        back_populates="user",
        foreign_keys="TimeOffRequest.user_id",
        cascade="all, delete-orphan",
        order_by="TimeOffRequest.created_at.desc()",
    )
    reviewed_time_off_requests = relationship(
        "TimeOffRequest",
        back_populates="reviewed_by",
        foreign_keys="TimeOffRequest.reviewed_by_id",
    )
    published_schedule_weeks = relationship(
        "DepartmentScheduleWeek",
        back_populates="published_by",
        foreign_keys="DepartmentScheduleWeek.published_by_id",
    )
    assigned_shifts = relationship(
        "Shift",
        back_populates="assigned_user",
        foreign_keys="Shift.assigned_user_id",
    )
    created_schedule_shifts = relationship(
        "Shift",
        back_populates="created_by",
        foreign_keys="Shift.created_by_id",
    )
    updated_schedule_shifts = relationship(
        "Shift",
        back_populates="updated_by",
        foreign_keys="Shift.updated_by_id",
    )
    schedule_shift_audits = relationship(
        "ShiftAudit",
        back_populates="changed_by",
        foreign_keys="ShiftAudit.changed_by_user_id",
    )
    schedule_view_receipts = relationship(
        "ScheduleWeekViewReceipt",
        back_populates="user",
        cascade="all, delete-orphan",
    )
    tradeboard_claims = relationship(
        "TradeboardClaim",
        back_populates="user",
        foreign_keys="TradeboardClaim.user_id",
        cascade="all, delete-orphan",
    )
    reviewed_tradeboard_claims = relationship(
        "TradeboardClaim",
        back_populates="reviewed_by",
        foreign_keys="TradeboardClaim.reviewed_by_id",
    )
    permission_groups = relationship(
        "PermissionGroup",
        secondary=user_permission_groups,
        back_populates="users",
        order_by="PermissionGroup.name",
    )
    sent_communications = relationship(
        "Communication",
        back_populates="sender",
        foreign_keys="Communication.sender_id",
        order_by="Communication.created_at.desc()",
    )
    communication_recipients = relationship(
        "CommunicationRecipient",
        back_populates="user",
        cascade="all, delete-orphan",
        order_by="CommunicationRecipient.created_at.desc()",
    )

    @property
    def is_super_admin(self) -> bool:
        return bool(self.is_admin)

    @property
    def preferred_name(self) -> str:
        return " ".join((self.display_name or "").split())

    @property
    def name_or_email(self) -> str:
        return self.preferred_name or self.email

    @property
    def display_label(self) -> str:
        preferred_name = self.preferred_name
        normalized_email = (self.email or "").strip()
        if preferred_name and preferred_name.casefold() != normalized_email.casefold():
            return f"{preferred_name} ({normalized_email})"
        return normalized_email

    @property
    def sort_key(self) -> str:
        return self.name_or_email.casefold()

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


class Department(db.Model):
    __tablename__ = "schedule_department"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False, unique=True)
    description = db.Column(db.Text, nullable=True)
    active = db.Column(
        db.Boolean, nullable=False, default=True, server_default="1"
    )
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

    positions = relationship(
        "ShiftPosition",
        back_populates="department",
        cascade="all, delete-orphan",
        order_by="ShiftPosition.sort_order.asc(), ShiftPosition.name.asc()",
    )
    memberships = relationship(
        "UserDepartmentMembership",
        back_populates="department",
        cascade="all, delete-orphan",
    )
    schedule_weeks = relationship(
        "DepartmentScheduleWeek",
        back_populates="department",
        cascade="all, delete-orphan",
        order_by="DepartmentScheduleWeek.week_start.desc()",
    )
    schedule_templates = relationship(
        "ScheduleTemplate",
        back_populates="department",
        cascade="all, delete-orphan",
        order_by="ScheduleTemplate.name.asc()",
    )

    __table_args__ = (db.Index("ix_schedule_department_active", "active"),)


class ShiftPosition(db.Model):
    __tablename__ = "schedule_shift_position"

    id = db.Column(db.Integer, primary_key=True)
    department_id = db.Column(
        db.Integer, db.ForeignKey("schedule_department.id"), nullable=False
    )
    name = db.Column(db.String(100), nullable=False)
    description = db.Column(db.Text, nullable=True)
    default_color = db.Column(db.String(20), nullable=True)
    active = db.Column(
        db.Boolean, nullable=False, default=True, server_default="1"
    )
    sort_order = db.Column(
        db.Integer, nullable=False, default=0, server_default="0"
    )
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

    department = relationship("Department", back_populates="positions")
    eligibilities = relationship(
        "UserPositionEligibility",
        back_populates="position",
        cascade="all, delete-orphan",
    )
    shifts = relationship("Shift", back_populates="position")
    schedule_templates = relationship(
        "ScheduleTemplate",
        back_populates="position",
        cascade="all, delete-orphan",
        order_by="ScheduleTemplate.name.asc()",
    )

    __table_args__ = (
        db.UniqueConstraint(
            "department_id",
            "name",
            name="uq_schedule_shift_position_department_name",
        ),
        db.Index("ix_schedule_shift_position_department", "department_id"),
        db.Index("ix_schedule_shift_position_active", "active"),
    )


class ScheduleTemplate(db.Model):
    __tablename__ = "schedule_template"

    SPAN_WEEK = "week"
    SPAN_MONTH = "month"
    SPAN_YEAR = "year"
    SPAN_CHOICES = (
        (SPAN_WEEK, "Week"),
        (SPAN_MONTH, "Month"),
        (SPAN_YEAR, "Year"),
    )

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    description = db.Column(db.Text, nullable=True)
    department_id = db.Column(
        db.Integer, db.ForeignKey("schedule_department.id"), nullable=False
    )
    position_id = db.Column(
        db.Integer, db.ForeignKey("schedule_shift_position.id"), nullable=False
    )
    span = db.Column(
        db.String(20),
        nullable=False,
        default=SPAN_WEEK,
        server_default=SPAN_WEEK,
    )
    active = db.Column(
        db.Boolean, nullable=False, default=True, server_default="1"
    )
    created_by_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)
    updated_by_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)
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

    department = relationship("Department", back_populates="schedule_templates")
    position = relationship("ShiftPosition", back_populates="schedule_templates")
    entries = relationship(
        "ScheduleTemplateEntry",
        back_populates="template",
        cascade="all, delete-orphan",
        order_by=(
            "ScheduleTemplateEntry.month_of_year.asc(), "
            "ScheduleTemplateEntry.day_of_month.asc(), "
            "ScheduleTemplateEntry.weekday.asc(), "
            "ScheduleTemplateEntry.start_time.asc(), "
            "ScheduleTemplateEntry.id.asc()"
        ),
    )
    created_by = relationship("User", foreign_keys=[created_by_id])
    updated_by = relationship("User", foreign_keys=[updated_by_id])

    __table_args__ = (
        db.Index("ix_schedule_template_department", "department_id"),
        db.Index("ix_schedule_template_position", "position_id"),
        db.Index("ix_schedule_template_span_active", "span", "active"),
    )

    @property
    def span_label(self) -> str:
        return dict(self.SPAN_CHOICES).get(self.span, self.span.title())


class ScheduleTemplateEntry(db.Model):
    __tablename__ = "schedule_template_entry"

    WEEKDAY_LABELS = (
        "Monday",
        "Tuesday",
        "Wednesday",
        "Thursday",
        "Friday",
        "Saturday",
        "Sunday",
    )
    MONTH_LABELS = (
        "",
        "January",
        "February",
        "March",
        "April",
        "May",
        "June",
        "July",
        "August",
        "September",
        "October",
        "November",
        "December",
    )

    id = db.Column(db.Integer, primary_key=True)
    template_id = db.Column(
        db.Integer, db.ForeignKey("schedule_template.id"), nullable=False
    )
    weekday = db.Column(db.Integer, nullable=True)
    day_of_month = db.Column(db.Integer, nullable=True)
    month_of_year = db.Column(db.Integer, nullable=True)
    assignment_mode = db.Column(
        db.String(20),
        nullable=False,
        default="assigned",
        server_default="assigned",
    )
    assigned_user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)
    start_time = db.Column(db.Time, nullable=False)
    end_time = db.Column(db.Time, nullable=False)
    paid_hours = db.Column(
        db.Float, nullable=False, default=0.0, server_default="0.0"
    )
    paid_hours_manual = db.Column(
        db.Boolean, nullable=False, default=False, server_default="0"
    )
    notes = db.Column(db.Text, nullable=True)
    color = db.Column(db.String(20), nullable=True)
    is_locked = db.Column(
        db.Boolean, nullable=False, default=False, server_default="0"
    )
    created_by_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)
    updated_by_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)
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

    template = relationship("ScheduleTemplate", back_populates="entries")
    assigned_user = relationship("User", foreign_keys=[assigned_user_id])
    created_by = relationship("User", foreign_keys=[created_by_id])
    updated_by = relationship("User", foreign_keys=[updated_by_id])

    __table_args__ = (
        db.Index("ix_schedule_template_entry_template", "template_id"),
        db.Index(
            "ix_schedule_template_entry_assigned_user", "assigned_user_id"
        ),
    )

    @property
    def occurrence_sort_key(self) -> tuple[int, int]:
        if self.template and self.template.span == ScheduleTemplate.SPAN_WEEK:
            return (self.weekday or 0, 0)
        if self.template and self.template.span == ScheduleTemplate.SPAN_MONTH:
            return (self.day_of_month or 0, 0)
        return (self.month_of_year or 0, self.day_of_month or 0)

    @property
    def occurrence_label(self) -> str:
        if self.template and self.template.span == ScheduleTemplate.SPAN_WEEK:
            if self.weekday is None or not 0 <= self.weekday < len(self.WEEKDAY_LABELS):
                return "Unset day"
            return self.WEEKDAY_LABELS[self.weekday]
        if self.template and self.template.span == ScheduleTemplate.SPAN_MONTH:
            return f"Day {self.day_of_month}" if self.day_of_month else "Unset day"
        month_label = (
            self.MONTH_LABELS[self.month_of_year]
            if self.month_of_year
            and 0 < self.month_of_year < len(self.MONTH_LABELS)
            else "Unset month"
        )
        if self.day_of_month:
            return f"{month_label} {self.day_of_month}"
        return month_label


class UserDepartmentMembership(db.Model):
    __tablename__ = "schedule_user_department_membership"

    ROLE_STAFF = "staff"
    ROLE_MANAGER = "manager"
    ROLE_GM = "gm"
    MANAGEMENT_ROLES = {ROLE_MANAGER, ROLE_GM}
    DEFAULT_ROLE_SUGGESTIONS = (ROLE_STAFF, ROLE_MANAGER, ROLE_GM)

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    department_id = db.Column(
        db.Integer, db.ForeignKey("schedule_department.id"), nullable=False
    )
    role = db.Column(
        db.String(50),
        nullable=False,
        default=ROLE_STAFF,
        server_default=ROLE_STAFF,
    )
    can_auto_assign = db.Column(
        db.Boolean,
        nullable=False,
        default=False,
        server_default="0",
    )
    is_primary = db.Column(
        db.Boolean, nullable=False, default=False, server_default="0"
    )
    reports_to_user_id = db.Column(
        db.Integer, db.ForeignKey("user.id"), nullable=True
    )
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

    user = relationship(
        "User",
        back_populates="department_memberships",
        foreign_keys=[user_id],
    )
    department = relationship("Department", back_populates="memberships")
    reports_to = relationship(
        "User",
        back_populates="managed_department_memberships",
        foreign_keys=[reports_to_user_id],
    )

    __table_args__ = (
        db.UniqueConstraint(
            "user_id",
            "department_id",
            name="uq_schedule_user_department_membership_user_department",
        ),
        db.Index("ix_schedule_user_department_membership_department", "department_id"),
        db.Index(
            "ix_schedule_user_department_membership_reports_to",
            "reports_to_user_id",
        ),
    )

    @classmethod
    def normalize_role(cls, value: str | None) -> str:
        normalized = " ".join(str(value or "").strip().lower().split())
        return normalized or cls.ROLE_STAFF

    @classmethod
    def is_gm_role(cls, value: str | None) -> bool:
        return cls.normalize_role(value) == cls.ROLE_GM

    @classmethod
    def is_management_role(cls, value: str | None) -> bool:
        return cls.normalize_role(value) in cls.MANAGEMENT_ROLES

    @classmethod
    def default_auto_assign_access_for_role(cls, value: str | None) -> bool:
        return cls.is_management_role(value)


class UserPositionEligibility(db.Model):
    __tablename__ = "schedule_user_position_eligibility"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    position_id = db.Column(
        db.Integer, db.ForeignKey("schedule_shift_position.id"), nullable=False
    )
    priority = db.Column(
        db.Integer, nullable=False, default=0, server_default="0"
    )
    active = db.Column(
        db.Boolean, nullable=False, default=True, server_default="1"
    )
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

    user = relationship("User", back_populates="position_eligibilities")
    position = relationship("ShiftPosition", back_populates="eligibilities")

    __table_args__ = (
        db.UniqueConstraint(
            "user_id",
            "position_id",
            name="uq_schedule_user_position_eligibility_user_position",
        ),
        db.Index("ix_schedule_user_position_eligibility_position", "position_id"),
    )


class DepartmentScheduleWeek(db.Model):
    __tablename__ = "schedule_department_week"

    id = db.Column(db.Integer, primary_key=True)
    department_id = db.Column(
        db.Integer, db.ForeignKey("schedule_department.id"), nullable=False
    )
    week_start = db.Column(db.Date, nullable=False)
    is_published = db.Column(
        db.Boolean, nullable=False, default=False, server_default="0"
    )
    current_version = db.Column(
        db.Integer, nullable=False, default=0, server_default="0"
    )
    published_at = db.Column(db.DateTime, nullable=True)
    unpublished_at = db.Column(db.DateTime, nullable=True)
    published_by_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)
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

    department = relationship("Department", back_populates="schedule_weeks")
    published_by = relationship(
        "User",
        back_populates="published_schedule_weeks",
        foreign_keys=[published_by_id],
    )
    shifts = relationship(
        "Shift",
        back_populates="schedule_week",
        cascade="all, delete-orphan",
        order_by="Shift.shift_date.asc(), Shift.start_time.asc(), Shift.id.asc()",
    )
    receipts = relationship(
        "ScheduleWeekViewReceipt",
        back_populates="schedule_week",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        db.UniqueConstraint(
            "department_id",
            "week_start",
            name="uq_schedule_department_week_department_week_start",
        ),
        db.Index("ix_schedule_department_week_published", "is_published"),
    )

    @property
    def week_end(self) -> date_cls:
        return self.week_start + timedelta(days=6)


class Shift(db.Model):
    __tablename__ = "schedule_shift"

    ASSIGNMENT_ASSIGNED = "assigned"
    ASSIGNMENT_OPEN = "open"
    ASSIGNMENT_TRADEBOARD = "tradeboard"

    id = db.Column(db.Integer, primary_key=True)
    schedule_week_id = db.Column(
        db.Integer, db.ForeignKey("schedule_department_week.id"), nullable=False
    )
    position_id = db.Column(
        db.Integer, db.ForeignKey("schedule_shift_position.id"), nullable=False
    )
    assigned_user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)
    location_id = db.Column(db.Integer, db.ForeignKey("location.id"), nullable=True)
    event_id = db.Column(db.Integer, db.ForeignKey("event.id"), nullable=True)
    shift_date = db.Column(db.Date, nullable=False)
    start_time = db.Column(db.Time, nullable=False)
    end_time = db.Column(db.Time, nullable=False)
    paid_hours = db.Column(
        db.Float, nullable=False, default=0.0, server_default="0.0"
    )
    paid_hours_manual = db.Column(
        db.Boolean, nullable=False, default=False, server_default="0"
    )
    notes = db.Column(db.Text, nullable=True)
    color = db.Column(db.String(20), nullable=True)
    assignment_mode = db.Column(
        db.String(20),
        nullable=False,
        default=ASSIGNMENT_ASSIGNED,
        server_default=ASSIGNMENT_ASSIGNED,
    )
    is_locked = db.Column(
        db.Boolean, nullable=False, default=False, server_default="0"
    )
    hourly_rate_snapshot = db.Column(
        db.Float, nullable=True, default=0.0, server_default="0.0"
    )
    live_version = db.Column(
        db.Integer, nullable=False, default=0, server_default="0"
    )
    created_by_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)
    updated_by_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)
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

    schedule_week = relationship("DepartmentScheduleWeek", back_populates="shifts")
    position = relationship("ShiftPosition", back_populates="shifts")
    assigned_user = relationship(
        "User",
        back_populates="assigned_shifts",
        foreign_keys=[assigned_user_id],
    )
    location = relationship("Location")
    event = relationship("Event")
    created_by = relationship(
        "User",
        back_populates="created_schedule_shifts",
        foreign_keys=[created_by_id],
    )
    updated_by = relationship(
        "User",
        back_populates="updated_schedule_shifts",
        foreign_keys=[updated_by_id],
    )
    audits = relationship(
        "ShiftAudit",
        back_populates="shift",
        cascade="all, delete-orphan",
        order_by="ShiftAudit.changed_at.desc()",
    )
    tradeboard_claims = relationship(
        "TradeboardClaim",
        back_populates="shift",
        cascade="all, delete-orphan",
        order_by="TradeboardClaim.created_at.desc()",
    )

    __table_args__ = (
        db.Index("ix_schedule_shift_week_date", "schedule_week_id", "shift_date"),
        db.Index("ix_schedule_shift_assigned_user", "assigned_user_id"),
        db.Index("ix_schedule_shift_position", "position_id"),
        db.Index("ix_schedule_shift_assignment_mode", "assignment_mode"),
    )

    @property
    def starts_at(self) -> datetime:
        return datetime.combine(self.shift_date, self.start_time)

    @property
    def ends_at(self) -> datetime:
        end_date = self.shift_date
        if self.end_time <= self.start_time:
            end_date = self.shift_date + timedelta(days=1)
        return datetime.combine(end_date, self.end_time)

    @property
    def duration_hours(self) -> float:
        return round((self.ends_at - self.starts_at).total_seconds() / 3600.0, 2)


class ShiftAudit(db.Model):
    __tablename__ = "schedule_shift_audit"

    id = db.Column(db.Integer, primary_key=True)
    shift_id = db.Column(
        db.Integer, db.ForeignKey("schedule_shift.id"), nullable=False
    )
    action = db.Column(db.String(50), nullable=False)
    version = db.Column(
        db.Integer, nullable=False, default=0, server_default="0"
    )
    summary = db.Column(db.Text, nullable=True)
    details = db.Column(db.JSON, nullable=True)
    changed_by_user_id = db.Column(
        db.Integer, db.ForeignKey("user.id"), nullable=True
    )
    changed_at = db.Column(
        db.DateTime,
        nullable=False,
        default=datetime.utcnow,
        server_default=func.now(),
    )

    shift = relationship("Shift", back_populates="audits")
    changed_by = relationship(
        "User",
        back_populates="schedule_shift_audits",
        foreign_keys=[changed_by_user_id],
    )

    __table_args__ = (
        db.Index("ix_schedule_shift_audit_shift", "shift_id"),
        db.Index("ix_schedule_shift_audit_changed_at", "changed_at"),
    )


class RecurringAvailabilityWindow(db.Model):
    __tablename__ = "schedule_recurring_availability"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    weekday = db.Column(db.Integer, nullable=False)
    start_time = db.Column(db.Time, nullable=False)
    end_time = db.Column(db.Time, nullable=False)
    note = db.Column(db.String(255), nullable=True)
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

    user = relationship("User", back_populates="recurring_availability_windows")

    __table_args__ = (
        db.Index(
            "ix_schedule_recurring_availability_user_weekday", "user_id", "weekday"
        ),
    )


class AvailabilityOverride(db.Model):
    __tablename__ = "schedule_availability_override"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    start_at = db.Column(db.DateTime, nullable=False)
    end_at = db.Column(db.DateTime, nullable=False)
    is_available = db.Column(
        db.Boolean, nullable=False, default=False, server_default="0"
    )
    note = db.Column(db.String(255), nullable=True)
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

    user = relationship("User", back_populates="availability_overrides")

    __table_args__ = (
        db.Index("ix_schedule_availability_override_user", "user_id"),
        db.Index("ix_schedule_availability_override_start", "start_at"),
    )


class TimeOffRequest(db.Model):
    __tablename__ = "schedule_time_off_request"

    STATUS_PENDING = "pending"
    STATUS_APPROVED = "approved"
    STATUS_DENIED = "denied"
    STATUS_CANCELLED = "cancelled"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    start_date = db.Column(db.Date, nullable=False)
    end_date = db.Column(db.Date, nullable=False)
    start_time = db.Column(db.Time, nullable=True)
    end_time = db.Column(db.Time, nullable=True)
    reason = db.Column(db.Text, nullable=False)
    manager_note = db.Column(db.Text, nullable=True)
    status = db.Column(
        db.String(20),
        nullable=False,
        default=STATUS_PENDING,
        server_default=STATUS_PENDING,
    )
    reviewed_by_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)
    reviewed_at = db.Column(db.DateTime, nullable=True)
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

    user = relationship(
        "User",
        back_populates="time_off_requests",
        foreign_keys=[user_id],
    )
    reviewed_by = relationship(
        "User",
        back_populates="reviewed_time_off_requests",
        foreign_keys=[reviewed_by_id],
    )

    __table_args__ = (
        db.Index("ix_schedule_time_off_request_user", "user_id"),
        db.Index("ix_schedule_time_off_request_status", "status"),
        db.Index("ix_schedule_time_off_request_start_end", "start_date", "end_date"),
    )

    @property
    def is_full_day(self) -> bool:
        return self.start_time is None and self.end_time is None


class ScheduleWeekViewReceipt(db.Model):
    __tablename__ = "schedule_week_view_receipt"

    id = db.Column(db.Integer, primary_key=True)
    schedule_week_id = db.Column(
        db.Integer, db.ForeignKey("schedule_department_week.id"), nullable=False
    )
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    first_seen_at = db.Column(db.DateTime, nullable=True)
    last_seen_at = db.Column(db.DateTime, nullable=True)
    last_seen_version = db.Column(
        db.Integer, nullable=False, default=0, server_default="0"
    )

    schedule_week = relationship(
        "DepartmentScheduleWeek", back_populates="receipts"
    )
    user = relationship("User", back_populates="schedule_view_receipts")

    __table_args__ = (
        db.UniqueConstraint(
            "schedule_week_id",
            "user_id",
            name="uq_schedule_week_view_receipt_week_user",
        ),
        db.Index("ix_schedule_week_view_receipt_user", "user_id"),
    )


class TradeboardClaim(db.Model):
    __tablename__ = "schedule_tradeboard_claim"

    STATUS_PENDING = "pending"
    STATUS_APPROVED = "approved"
    STATUS_REJECTED = "rejected"
    STATUS_CANCELLED = "cancelled"

    id = db.Column(db.Integer, primary_key=True)
    shift_id = db.Column(
        db.Integer, db.ForeignKey("schedule_shift.id"), nullable=False
    )
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    status = db.Column(
        db.String(20),
        nullable=False,
        default=STATUS_PENDING,
        server_default=STATUS_PENDING,
    )
    manager_note = db.Column(db.Text, nullable=True)
    reviewed_by_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)
    reviewed_at = db.Column(db.DateTime, nullable=True)
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

    shift = relationship("Shift", back_populates="tradeboard_claims")
    user = relationship(
        "User",
        back_populates="tradeboard_claims",
        foreign_keys=[user_id],
    )
    reviewed_by = relationship(
        "User",
        back_populates="reviewed_tradeboard_claims",
        foreign_keys=[reviewed_by_id],
    )

    __table_args__ = (
        db.UniqueConstraint(
            "shift_id",
            "user_id",
            name="uq_schedule_tradeboard_claim_shift_user",
        ),
        db.Index("ix_schedule_tradeboard_claim_status", "status"),
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
    default_playlist_id = db.Column(
        db.Integer, db.ForeignKey("playlist.id"), nullable=True
    )
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
    default_playlist = relationship(
        "Playlist",
        back_populates="locations",
        foreign_keys="Location.default_playlist_id",
    )
    menu_assignments = relationship(
        "MenuAssignment",
        back_populates="location",
        order_by="MenuAssignment.assigned_at.desc()",
        cascade="all, delete-orphan",
    )
    displays = relationship(
        "Display",
        back_populates="location",
        cascade="all, delete-orphan",
        order_by="Display.name.asc()",
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
    playlist_items = relationship("PlaylistItem", back_populates="menu")


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


class Playlist(db.Model):
    __tablename__ = "playlist"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False, unique=True)
    description = db.Column(db.Text, nullable=True)
    archived = db.Column(
        db.Boolean, nullable=False, default=False, server_default="0"
    )
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

    items = relationship(
        "PlaylistItem",
        back_populates="playlist",
        cascade="all, delete-orphan",
        order_by="PlaylistItem.position.asc(), PlaylistItem.id.asc()",
    )
    locations = relationship(
        "Location",
        back_populates="default_playlist",
        foreign_keys="Location.default_playlist_id",
    )
    displays = relationship(
        "Display",
        back_populates="playlist_override",
        foreign_keys="Display.playlist_override_id",
    )


class PlaylistItem(db.Model):
    __tablename__ = "playlist_item"

    SOURCE_LOCATION_MENU = "location_menu"
    SOURCE_MENU = "menu"

    id = db.Column(db.Integer, primary_key=True)
    playlist_id = db.Column(db.Integer, db.ForeignKey("playlist.id"), nullable=False)
    position = db.Column(
        db.Integer, nullable=False, default=0, server_default="0"
    )
    source_type = db.Column(
        db.String(32),
        nullable=False,
        default=SOURCE_LOCATION_MENU,
        server_default=SOURCE_LOCATION_MENU,
    )
    menu_id = db.Column(db.Integer, db.ForeignKey("menu.id"), nullable=True)
    duration_seconds = db.Column(
        db.Integer, nullable=False, default=15, server_default="15"
    )
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

    playlist = relationship("Playlist", back_populates="items")
    menu = relationship("Menu", back_populates="playlist_items")

    __table_args__ = (
        db.Index("ix_playlist_item_playlist_position", "playlist_id", "position"),
    )


class BoardTemplate(db.Model):
    __tablename__ = "signage_board_template"

    PANEL_NONE = "none"
    PANEL_LEFT = "left"
    PANEL_RIGHT = "right"

    THEME_AURORA = "aurora"
    THEME_MIDNIGHT = "midnight"
    THEME_SUNSET = "sunset"
    THEME_CONCOURSE = "concourse"
    GRID_COLUMNS = 24
    GRID_ROWS = 12

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False, unique=True)
    description = db.Column(db.Text, nullable=True)
    theme = db.Column(
        db.String(32),
        nullable=False,
        default=THEME_AURORA,
        server_default=THEME_AURORA,
    )
    canvas_width = db.Column(
        db.Integer, nullable=False, default=1920, server_default="1920"
    )
    canvas_height = db.Column(
        db.Integer, nullable=False, default=1080, server_default="1080"
    )
    menu_columns = db.Column(
        db.Integer, nullable=False, default=3, server_default="3"
    )
    menu_rows = db.Column(
        db.Integer, nullable=False, default=4, server_default="4"
    )
    show_prices = db.Column(
        db.Boolean, nullable=False, default=True, server_default="1"
    )
    show_menu_description = db.Column(
        db.Boolean, nullable=False, default=False, server_default="0"
    )
    show_page_indicator = db.Column(
        db.Boolean, nullable=False, default=True, server_default="1"
    )
    brand_label = db.Column(db.String(80), nullable=True)
    brand_name = db.Column(db.String(120), nullable=True)
    side_panel_position = db.Column(
        db.String(16),
        nullable=False,
        default=PANEL_NONE,
        server_default=PANEL_NONE,
    )
    side_panel_width_percent = db.Column(
        db.Integer, nullable=False, default=30, server_default="30"
    )
    side_title = db.Column(db.String(120), nullable=True)
    side_body = db.Column(db.Text, nullable=True)
    side_image_url = db.Column(db.Text, nullable=True)
    footer_text = db.Column(db.String(255), nullable=True)
    archived = db.Column(
        db.Boolean, nullable=False, default=False, server_default="0"
    )
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

    blocks = relationship(
        "BoardTemplateBlock",
        back_populates="board_template",
        cascade="all, delete-orphan",
        order_by="BoardTemplateBlock.position.asc(), BoardTemplateBlock.id.asc()",
    )
    displays = relationship("Display", back_populates="board_template")

    __table_args__ = (db.Index("ix_signage_board_template_archived", "archived"),)


class BoardTemplateBlock(db.Model):
    __tablename__ = "signage_board_template_block"

    TYPE_MENU = "menu"
    TYPE_TEXT = "text"
    TYPE_IMAGE = "image"
    TYPE_VIDEO = "video"

    id = db.Column(db.Integer, primary_key=True)
    board_template_id = db.Column(
        db.Integer,
        db.ForeignKey("signage_board_template.id"),
        nullable=False,
    )
    media_asset_id = db.Column(
        db.Integer,
        db.ForeignKey("signage_media_asset.id"),
        nullable=True,
    )
    position = db.Column(
        db.Integer, nullable=False, default=0, server_default="0"
    )
    block_type = db.Column(
        db.String(32),
        nullable=False,
        default=TYPE_MENU,
        server_default=TYPE_MENU,
    )
    width_units = db.Column(
        db.Integer, nullable=False, default=6, server_default="6"
    )
    title = db.Column(db.String(120), nullable=True)
    body = db.Column(db.Text, nullable=True)
    media_url = db.Column(db.Text, nullable=True)
    grid_x = db.Column(
        db.Integer, nullable=False, default=1, server_default="1"
    )
    grid_y = db.Column(
        db.Integer, nullable=False, default=1, server_default="1"
    )
    grid_width = db.Column(
        db.Integer, nullable=False, default=12, server_default="12"
    )
    grid_height = db.Column(
        db.Integer, nullable=False, default=10, server_default="10"
    )
    menu_columns = db.Column(
        db.Integer, nullable=False, default=2, server_default="2"
    )
    menu_rows = db.Column(
        db.Integer, nullable=False, default=4, server_default="4"
    )
    show_title = db.Column(
        db.Boolean, nullable=False, default=True, server_default="1"
    )
    show_prices = db.Column(
        db.Boolean, nullable=False, default=True, server_default="1"
    )
    show_menu_description = db.Column(
        db.Boolean, nullable=False, default=False, server_default="0"
    )
    selected_product_ids = db.Column(db.Text, nullable=True)
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

    board_template = relationship("BoardTemplate", back_populates="blocks")
    media_asset = relationship("SignageMediaAsset", back_populates="blocks")

    __table_args__ = (
        db.Index(
            "ix_signage_board_template_block_template_position",
            "board_template_id",
            "position",
        ),
        db.Index("ix_signage_board_template_block_media_asset_id", "media_asset_id"),
    )

    @property
    def selected_product_id_list(self) -> list[int]:
        if not self.selected_product_ids:
            return []
        values: list[int] = []
        seen: set[int] = set()
        for raw_value in self.selected_product_ids.split(","):
            raw_value = raw_value.strip()
            if not raw_value:
                continue
            try:
                product_id = int(raw_value)
            except (TypeError, ValueError):
                continue
            if product_id in seen:
                continue
            seen.add(product_id)
            values.append(product_id)
        return values


class SignageMediaAsset(db.Model):
    __tablename__ = "signage_media_asset"

    TYPE_IMAGE = "image"
    TYPE_VIDEO = "video"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=True)
    original_filename = db.Column(db.String(255), nullable=False)
    media_type = db.Column(db.String(16), nullable=False)
    content_type = db.Column(db.String(120), nullable=True)
    file_size_bytes = db.Column(
        db.Integer, nullable=False, default=0, server_default="0"
    )
    sha256 = db.Column(db.String(64), nullable=False)
    storage_path = db.Column(db.String(1024), nullable=False)
    uploaded_by = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)
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

    uploader = relationship("User", foreign_keys=[uploaded_by])
    blocks = relationship("BoardTemplateBlock", back_populates="media_asset")

    __table_args__ = (
        db.CheckConstraint(
            "media_type IN ('image', 'video')",
            name="ck_signage_media_asset_media_type",
        ),
        db.Index("ix_signage_media_asset_media_type", "media_type"),
        db.Index("ix_signage_media_asset_uploaded_by", "uploaded_by"),
        db.Index("ix_signage_media_asset_sha256", "sha256"),
    )

    @property
    def display_name(self) -> str:
        if self.name:
            return self.name
        return self.original_filename


def _generate_display_browser_code() -> str:
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    display_model = globals().get("Display")
    while True:
        code = "".join(secrets.choice(alphabet) for _ in range(6))
        if display_model is None or not display_model.query.filter_by(
            browser_code=code
        ).first():
            return code


class Display(db.Model):
    __tablename__ = "signage_display"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    location_id = db.Column(db.Integer, db.ForeignKey("location.id"), nullable=False)
    playlist_override_id = db.Column(
        db.Integer, db.ForeignKey("playlist.id"), nullable=True
    )
    board_template_id = db.Column(
        db.Integer, db.ForeignKey("signage_board_template.id"), nullable=True
    )
    public_token = db.Column(
        db.String(64),
        unique=True,
        nullable=False,
        default=lambda: secrets.token_urlsafe(24),
    )
    browser_code = db.Column(
        db.String(8),
        unique=True,
        nullable=False,
        default=_generate_display_browser_code,
    )
    board_columns = db.Column(
        db.Integer, nullable=False, default=3, server_default="3"
    )
    board_rows = db.Column(
        db.Integer, nullable=False, default=4, server_default="4"
    )
    show_prices = db.Column(
        db.Boolean, nullable=False, default=True, server_default="1"
    )
    show_menu_description = db.Column(
        db.Boolean, nullable=False, default=False, server_default="0"
    )
    selected_product_ids = db.Column(db.Text, nullable=True)
    activation_code = db.Column(db.String(12), nullable=True, unique=True)
    activation_code_expires_at = db.Column(db.DateTime, nullable=True)
    last_activated_at = db.Column(db.DateTime, nullable=True)
    last_seen_at = db.Column(db.DateTime, nullable=True)
    last_seen_ip = db.Column(db.String(64), nullable=True)
    last_seen_user_agent = db.Column(db.String(255), nullable=True)
    archived = db.Column(
        db.Boolean, nullable=False, default=False, server_default="0"
    )
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

    location = relationship("Location", back_populates="displays")
    playlist_override = relationship(
        "Playlist",
        back_populates="displays",
        foreign_keys=[playlist_override_id],
    )
    board_template = relationship("BoardTemplate", back_populates="displays")

    __table_args__ = (
        db.Index("ix_signage_display_location_archived", "location_id", "archived"),
        db.Index("ix_signage_display_last_seen_at", "last_seen_at"),
        db.Index("ix_signage_display_board_template_id", "board_template_id"),
    )

    @property
    def effective_playlist(self) -> Optional[Playlist]:
        if self.playlist_override is not None:
            return self.playlist_override
        if self.location is None:
            return None
        return self.location.default_playlist

    @property
    def effective_board_template(self) -> Optional[BoardTemplate]:
        return self.board_template

    @property
    def is_online(self) -> bool:
        if self.last_seen_at is None:
            return False
        threshold_seconds = 120
        if has_app_context():
            threshold_seconds = int(
                current_app.config.get("DISPLAY_ONLINE_THRESHOLD_SECONDS", 120)
            )
        return (
            datetime.utcnow() - self.last_seen_at
        ).total_seconds() <= threshold_seconds

    @property
    def has_active_activation_code(self) -> bool:
        if not self.activation_code or self.activation_code_expires_at is None:
            return False
        return self.activation_code_expires_at >= datetime.utcnow()

    @property
    def selected_product_id_list(self) -> list[int]:
        if not self.selected_product_ids:
            return []
        values: list[int] = []
        seen: set[int] = set()
        for raw_value in self.selected_product_ids.split(","):
            raw_value = raw_value.strip()
            if not raw_value:
                continue
            try:
                product_id = int(raw_value)
            except (TypeError, ValueError):
                continue
            if product_id in seen:
                continue
            seen.add(product_id)
            values.append(product_id)
        return values


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
    STATUS_REQUESTED = "requested"
    STATUS_ORDERED = "ordered"
    STATUS_RECEIVED = "received"

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
    status = db.Column(
        db.String(20),
        nullable=False,
        default=STATUS_REQUESTED,
        server_default=STATUS_REQUESTED,
    )
    received = db.Column(db.Boolean, default=False, nullable=False)
    items = relationship(
        "PurchaseOrderItem",
        backref="purchase_order",
        cascade="all, delete-orphan",
        order_by="PurchaseOrderItem.position",
    )
    vendor = relationship("Vendor", backref="purchase_orders")

    __table_args__ = (
        db.CheckConstraint(
            "status IN ('requested', 'ordered', 'received')",
            name="ck_purchase_order_status",
        ),
        db.Index("ix_purchase_order_status", "status"),
    )

    @property
    def purchase_status(self) -> str:
        if self.received or self.status == self.STATUS_RECEIVED:
            return self.STATUS_RECEIVED
        if self.status == self.STATUS_ORDERED:
            return self.STATUS_ORDERED
        return self.STATUS_REQUESTED

    @property
    def purchase_status_label(self) -> str:
        return self.purchase_status.title()

    @property
    def purchase_status_badge_class(self) -> str:
        return {
            self.STATUS_REQUESTED: "text-bg-secondary",
            self.STATUS_ORDERED: "text-bg-primary",
            self.STATUS_RECEIVED: "text-bg-success",
        }.get(self.purchase_status, "text-bg-secondary")

    @property
    def can_mark_ordered(self) -> bool:
        return not self.received and self.purchase_status == self.STATUS_REQUESTED


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
    vendor_sku = db.Column(db.String(100), nullable=True)
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
    vendor_sku = db.Column(db.String(100), nullable=True)
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


class Communication(db.Model):
    __tablename__ = "communication"

    KIND_MESSAGE = "message"
    KIND_BULLETIN = "bulletin"

    AUDIENCE_USERS = "users"
    AUDIENCE_DEPARTMENT = "department"
    AUDIENCE_ALL = "all"

    id = db.Column(db.Integer, primary_key=True)
    kind = db.Column(
        db.String(20),
        nullable=False,
        default=KIND_MESSAGE,
        server_default=KIND_MESSAGE,
    )
    sender_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    department_id = db.Column(
        db.Integer, db.ForeignKey("schedule_department.id"), nullable=True
    )
    audience_type = db.Column(db.String(20), nullable=False)
    audience_snapshot = db.Column(db.JSON, nullable=True)
    subject = db.Column(db.String(200), nullable=False)
    body = db.Column(db.Text, nullable=False)
    pinned = db.Column(
        db.Boolean, default=False, nullable=False, server_default="0"
    )
    active = db.Column(
        db.Boolean, default=True, nullable=False, server_default="1"
    )
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

    sender = relationship(
        "User",
        back_populates="sent_communications",
        foreign_keys=[sender_id],
    )
    department = relationship("Department")
    recipients = relationship(
        "CommunicationRecipient",
        back_populates="communication",
        cascade="all, delete-orphan",
        order_by="CommunicationRecipient.created_at.asc()",
    )

    __table_args__ = (
        db.Index("ix_communication_kind_created", "kind", "created_at"),
        db.Index("ix_communication_department", "department_id"),
        db.Index("ix_communication_sender", "sender_id"),
        db.Index("ix_communication_active_pinned", "active", "pinned"),
    )

    @property
    def is_bulletin(self) -> bool:
        return self.kind == self.KIND_BULLETIN


class CommunicationRecipient(db.Model):
    __tablename__ = "communication_recipient"

    id = db.Column(db.Integer, primary_key=True)
    communication_id = db.Column(
        db.Integer, db.ForeignKey("communication.id"), nullable=False
    )
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    read_at = db.Column(db.DateTime, nullable=True)
    archived_at = db.Column(db.DateTime, nullable=True)
    deleted_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(
        db.DateTime,
        nullable=False,
        default=datetime.utcnow,
        server_default=func.now(),
    )

    communication = relationship("Communication", back_populates="recipients")
    user = relationship("User", back_populates="communication_recipients")

    __table_args__ = (
        db.UniqueConstraint(
            "communication_id",
            "user_id",
            name="uq_communication_recipient_communication_user",
        ),
        db.Index("ix_communication_recipient_user", "user_id"),
        db.Index(
            "ix_communication_recipient_user_read",
            "user_id",
            "read_at",
        ),
        db.Index(
            "ix_communication_recipient_user_archived",
            "user_id",
            "archived_at",
        ),
        db.Index(
            "ix_communication_recipient_user_deleted",
            "user_id",
            "deleted_at",
        ),
    )

    def mark_read(self) -> None:
        if self.read_at is None:
            self.read_at = datetime.utcnow()

    def archive(self) -> None:
        if self.archived_at is None:
            self.archived_at = datetime.utcnow()

    def restore(self) -> None:
        self.archived_at = None

    def delete_for_user(self) -> None:
        if self.deleted_at is None:
            self.deleted_at = datetime.utcnow()


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
    STATUS_PENDING = "pending"
    STATUS_NEEDS_MAPPING = "needs_mapping"
    STATUS_APPROVED = "approved"
    STATUS_REVERSED = "reversed"
    STATUS_DELETED = "deleted"
    STATUS_FAILED = "failed"
    STATUS_IGNORED = "ignored"

    id = db.Column(db.Integer, primary_key=True)
    source_provider = db.Column(db.String(100), nullable=False)
    message_id = db.Column(db.String(255), nullable=False)
    attachment_filename = db.Column(db.String(255), nullable=False)
    attachment_sha256 = db.Column(db.String(64), nullable=False)
    attachment_storage_path = db.Column(db.String(1024), nullable=True)
    sales_date = db.Column(
        db.Date,
        nullable=True,
        default=lambda: (datetime.utcnow() - timedelta(days=1)).date(),
    )
    received_at = db.Column(
        db.DateTime,
        nullable=False,
        default=datetime.utcnow,
        server_default=func.now(),
    )
    status = db.Column(
        db.String(32),
        nullable=False,
        default=STATUS_PENDING,
        server_default=STATUS_PENDING,
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
            "status IN ('pending', 'needs_mapping', 'approved', 'reversed', 'deleted', 'failed', 'ignored')",
            name="ck_pos_sales_import_status",
        ),
        db.Index("ix_pos_sales_import_status_received_at", "status", "received_at"),
        db.Index("ix_pos_sales_import_received_at", "received_at"),
        db.Index("ix_pos_sales_import_sales_date", "sales_date"),
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
    event_location_id = db.Column(
        db.Integer, db.ForeignKey("event_location.id"), nullable=True
    )
    total_quantity = db.Column(db.Float, nullable=False, default=0.0, server_default="0.0")
    net_inc = db.Column(db.Float, nullable=False, default=0.0, server_default="0.0")
    discounts_abs = db.Column(db.Float, nullable=False, default=0.0, server_default="0.0")
    computed_total = db.Column(db.Float, nullable=False, default=0.0, server_default="0.0")
    parse_index = db.Column(db.Integer, nullable=False)
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

    sales_import = relationship("PosSalesImport", back_populates="locations")
    location = relationship("Location")
    event_location = relationship("EventLocation")
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
        db.Index("ix_pos_sales_import_location_event_location_id", "event_location_id"),
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
    POS_SALES_IMPORT_INTERVAL = "POS_SALES_IMPORT_INTERVAL"
    MENU_FEED_API_TOKEN = "MENU_FEED_API_TOKEN"
    POS_SALES_IMPORT_INTERVAL_UNITS = ("hour", "day", "week")
    DEFAULT_POS_SALES_IMPORT_INTERVAL = {
        "value": 1,
        "unit": "day",
    }
    DEFAULT_PURCHASE_IMPORT_VENDORS = [
        "SYSCO",
        "PRATTS",
        "CENTRAL SUPPLY",
        "MANITOBA LIQUOR & LOTTERIES",
    ]

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

    @classmethod
    def get_pos_sales_import_interval(cls) -> dict[str, object]:
        """Return the configured POS sales import lookback interval."""

        setting = cls.query.filter_by(name=cls.POS_SALES_IMPORT_INTERVAL).first()
        default_value = int(cls.DEFAULT_POS_SALES_IMPORT_INTERVAL["value"])
        default_unit = str(cls.DEFAULT_POS_SALES_IMPORT_INTERVAL["unit"])
        if setting is None or not setting.value:
            return {"value": default_value, "unit": default_unit}

        try:
            payload = json.loads(setting.value)
        except (TypeError, ValueError):
            return {"value": default_value, "unit": default_unit}
        if not isinstance(payload, dict):
            return {"value": default_value, "unit": default_unit}

        try:
            value = int(payload.get("value", default_value))
        except (TypeError, ValueError):
            value = default_value
        if value < 1:
            value = default_value

        unit = str(payload.get("unit", default_unit) or default_unit).strip().lower()
        if unit not in cls.POS_SALES_IMPORT_INTERVAL_UNITS:
            unit = default_unit

        return {"value": value, "unit": unit}

    @classmethod
    def set_pos_sales_import_interval(cls, *, value: int, unit: str):
        """Persist the POS sales import lookback interval."""

        try:
            cleaned_value = int(value)
        except (TypeError, ValueError):
            cleaned_value = int(cls.DEFAULT_POS_SALES_IMPORT_INTERVAL["value"])
        if cleaned_value < 1:
            cleaned_value = int(cls.DEFAULT_POS_SALES_IMPORT_INTERVAL["value"])

        cleaned_unit = str(unit or "").strip().lower()
        if cleaned_unit not in cls.POS_SALES_IMPORT_INTERVAL_UNITS:
            cleaned_unit = str(cls.DEFAULT_POS_SALES_IMPORT_INTERVAL["unit"])

        setting = cls.query.filter_by(name=cls.POS_SALES_IMPORT_INTERVAL).first()
        if setting is None:
            setting = cls(name=cls.POS_SALES_IMPORT_INTERVAL)
            db.session.add(setting)

        setting.value = json.dumps(
            {
                "value": cleaned_value,
                "unit": cleaned_unit,
            }
        )
        return setting

    @classmethod
    def get_menu_feed_api_token(cls) -> str:
        """Return the configured menu-feed API token."""

        setting = cls.query.filter_by(name=cls.MENU_FEED_API_TOKEN).first()
        if setting is None or not setting.value:
            return ""
        return setting.value.strip()

    @classmethod
    def set_menu_feed_api_token(cls, token: str):
        """Persist the menu-feed API token."""

        setting = cls.query.filter_by(name=cls.MENU_FEED_API_TOKEN).first()
        if setting is None:
            setting = cls(name=cls.MENU_FEED_API_TOKEN)
            db.session.add(setting)
        setting.value = (token or "").strip()
        return setting
