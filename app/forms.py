import os
from datetime import date
from decimal import Decimal, InvalidOperation
from functools import lru_cache
from datetime import datetime
from zoneinfo import available_timezones

from flask import g
from flask_wtf import FlaskForm
from flask_wtf.file import FileAllowed, FileRequired
from sqlalchemy import func, or_
from sqlalchemy.orm import selectinload
from wtforms import (
    BooleanField,
    DateField,
    DateTimeLocalField,
    DecimalField as WTFormsDecimalField,
    FieldList,
    FileField,
    FormField,
    HiddenField,
    IntegerField,
    PasswordField,
    SelectField,
    SelectMultipleField,
    StringField,
    TextAreaField,
    SubmitField,
    TimeField,
)
from wtforms.validators import (
    DataRequired,
    Email,
    EqualTo,
    InputRequired,
    Length,
    NumberRange,
    Optional,
    ValidationError,
)
from wtforms.widgets import CheckboxInput, ListWidget, TextInput

from app import db
from app.models import (
    BoardTemplate,
    BoardTemplateBlock,
    Department,
    EquipmentAsset,
    EquipmentCategory,
    EquipmentIntakeBatch,
    EquipmentMaintenanceIssue,
    EquipmentModel,
    Display,
    Event,
    EventLocation,
    GLCode,
    Item,
    ItemUnit,
    Location,
    Menu,
    PermissionGroup,
    Playlist,
    PlaylistItem,
    Product,
    PurchaseInvoice,
    PurchaseOrder,
    ScheduleTemplate,
    ShiftPosition,
    SignageMediaAsset,
    User,
    UserDepartmentMembership,
    Vendor,
)
from app.utils.numeric import (
    ExpressionParsingError,
    evaluate_math_expression,
    looks_like_expression,
)
from app.utils.units import (
    BASE_UNIT_CHOICES,
    BASE_UNITS,
    get_allowed_target_units,
    get_unit_label,
)

# Uploaded backup files are capped at 10MB to prevent excessive memory usage
MAX_BACKUP_SIZE = 10 * 1024 * 1024  # 10 MB


PURCHASE_RECEIVE_DEPARTMENT_CONFIG = [
    ("Kitchen", "Kitchen", "receive_default_kitchen"),
    ("Concessions", "Concessions", "receive_default_concessions"),
    ("Banquets", "Banquets", "receive_default_banquets"),
    ("Beverages", "Beverages", "receive_default_beverages"),
    ("Office", "Office", "receive_default_office"),
    ("Other", "Other", "receive_default_other"),
]

PURCHASE_RECEIVE_DEPARTMENT_CHOICES = [
    (key, label) for key, label, _ in PURCHASE_RECEIVE_DEPARTMENT_CONFIG
]


class ExpressionDecimalField(WTFormsDecimalField):
    """Decimal field that supports simple math expressions prefixed with '='."""

    expression_prefix = "="
    widget = TextInput()

    _CURRENCY_SYMBOLS = "$€£¥₽₩₹₺"

    def __init__(self, *args, render_kw=None, **kwargs):
        render_kw = dict(render_kw or {})
        render_kw.setdefault("type", "text")
        render_kw.setdefault("data-numeric-input", "1")
        render_kw.setdefault("inputmode", "text")
        render_kw.setdefault("autocapitalize", "off")
        render_kw.setdefault("autocorrect", "off")
        render_kw.setdefault("spellcheck", "false")
        super().__init__(*args, render_kw=render_kw, **kwargs)

    @classmethod
    def _normalise_plain_number(cls, text):
        """Return a plain numeric string for formatted monetary input.

        Users frequently enter values such as ``"1,234.50"`` or
        ``"$1 234,50"`` when the form is rendered in a browser.  Those values
        are intuitive for humans but ``Decimal`` cannot parse them directly
        because of the thousands separators, currency symbols, or locale
        specific decimal separators.  This helper strips those presentation
        characters and converts the value into a canonical representation that
        :class:`decimal.Decimal` can understand.
        """

        if not text:
            return None

        cleaned = text.strip()
        if not cleaned:
            return None

        negative = False
        if cleaned.startswith("(") and cleaned.endswith(")"):
            negative = True
            cleaned = cleaned[1:-1]
        cleaned = cleaned.strip()

        # Remove leading/trailing currency symbols.
        while cleaned and cleaned[0] in cls._CURRENCY_SYMBOLS:
            cleaned = cleaned[1:].lstrip()
        while cleaned and cleaned[-1] in cls._CURRENCY_SYMBOLS:
            cleaned = cleaned[:-1].rstrip()

        if not cleaned:
            return None

        cleaned = cleaned.replace("\u00a0", " ")

        decimal_is_comma = False
        if "," in cleaned and "." in cleaned:
            last_comma = cleaned.rfind(",")
            last_dot = cleaned.rfind(".")
            if last_dot < last_comma:
                decimal_is_comma = True
        elif "," in cleaned:
            comma_index = cleaned.rfind(",")
            fractional_length = len(cleaned) - comma_index - 1
            decimal_is_comma = 0 < fractional_length <= 2

        cleaned = cleaned.replace("_", "")
        cleaned = cleaned.replace(" ", "")

        if decimal_is_comma:
            cleaned = cleaned.replace(".", "")
            cleaned = cleaned.replace(",", ".")
        else:
            cleaned = cleaned.replace(",", "")

        if not cleaned:
            return None

        if negative:
            cleaned = f"-{cleaned}"

        return cleaned

    def process_formdata(self, valuelist):
        if not valuelist:
            return super().process_formdata(valuelist)

        raw_value = valuelist[0]
        if raw_value is None:
            return super().process_formdata(valuelist)

        text = str(raw_value).strip()
        if not text:
            return super().process_formdata(valuelist)

        if text.startswith(self.expression_prefix):
            expression = text[len(self.expression_prefix) :].strip()
            if not expression:
                self.data = None
                raise ValueError("Enter a calculation after '='.")
            try:
                self.data = evaluate_math_expression(expression)
            except ExpressionParsingError as exc:
                self.data = None
                raise ValueError(str(exc)) from exc
            self.raw_data = valuelist
            return

        normalised = self._normalise_plain_number(text)
        if normalised and normalised != text:
            try:
                Decimal(normalised)
            except (InvalidOperation, ValueError):
                pass
            else:
                super().process_formdata([normalised])
                # Preserve the user's original input when re-rendering the
                # form so that validation errors elsewhere keep their data.
                self.raw_data = valuelist
                return

        if looks_like_expression(text):
            self.data = None
            raise ValueError("To enter a calculation, start the value with '='.")

        super().process_formdata(valuelist)


# Replace the imported DecimalField with the enhanced version for local use.
DecimalField = ExpressionDecimalField


class FlexibleDateField(DateField):
    """Date field that accepts multiple string formats.

    The flatpickr widget used in the UI submits dates formatted as
    ``"F j, Y"`` (for example, ``"July 1, 2024"``) when the alternative
    input is enabled.  Flask-WTF's :class:`~wtforms.fields.DateField`
    expects the primary ``format`` (``"%Y-%m-%d"`` by default) and will
    otherwise raise a validation error.  This subclass attempts to parse
    several common formats so the server can handle both the original
    ``YYYY-MM-DD`` values and the human-friendly flatpickr values.
    """

    def __init__(self, *args, alt_formats=None, **kwargs):
        super().__init__(*args, **kwargs)
        if alt_formats is None:
            alt_formats = ("%B %d, %Y", "%b %d, %Y")
        self.alt_formats = alt_formats

    def process_formdata(self, valuelist):
        if valuelist:
            raw_value = valuelist[0]
            if raw_value is not None:
                text = str(raw_value).strip()
                if text:
                    base_formats = (
                        list(self.format)
                        if isinstance(self.format, (list, tuple))
                        else [self.format]
                    )
                    for fmt in (*base_formats, *self.alt_formats):
                        try:
                            self.data = datetime.strptime(text, fmt).date()
                        except ValueError:
                            continue
                        else:
                            self.raw_data = [text]
                            return
                    self.data = None
                    raise ValueError(self.gettext("Not a valid date value"))
        super().process_formdata(valuelist)


def load_item_choices():
    """Return a list of active item choices, cached per request."""
    if "item_choices" not in g:
        g.item_choices = [
            (i.id, i.name) for i in Item.query.filter_by(archived=False).all()
        ]
    return g.item_choices


def load_unit_choices():
    """Return a list of item unit choices."""
    return [(u.id, u.name) for u in ItemUnit.query.all()]


def load_product_choices():
    """Return a list of product choices, cached per request."""
    if "product_choices" not in g:
        g.product_choices = [
            (p.id, p.name) for p in Product.query.order_by(Product.name).all()
        ]
    return g.product_choices


def load_event_choices():
    """Return event choices for reports, cached per request."""
    if "event_choices" not in g:
        events = Event.query.order_by(Event.start_date.desc(), Event.name).all()
        g.event_choices = [
            (
                event.id,
                (
                    f"{event.name} "
                    f"({event.start_date.isoformat()} to {event.end_date.isoformat()}) "
                    f"- {'Closed' if event.closed else 'Open'}"
                ),
            )
            for event in events
        ]
    return g.event_choices


def load_location_menu_product_choices(location_id: int | None):
    """Return product choices for a location's current menu when available."""
    if not location_id:
        return load_product_choices()
    location = db.session.get(Location, int(location_id))
    if location is None or location.current_menu is None:
        return load_product_choices()
    return [
        (product.id, product.name)
        for product in sorted(
            location.current_menu.products, key=lambda product: (product.name.lower(), product.id)
        )
    ] or load_product_choices()


def load_menu_choices(include_blank: bool = True):
    """Return menu options for selection fields."""

    menus = Menu.query.order_by(Menu.name).all()
    choices = [(menu.id, menu.name) for menu in menus]
    if include_blank:
        return [(0, "No Menu")] + choices
    return choices


def load_playlist_choices(include_blank: bool = True):
    """Return playlist options for selection fields."""

    playlists = Playlist.query.order_by(Playlist.name).all()
    choices = [
        (
            playlist.id,
            f"{playlist.name} (archived)" if playlist.archived else playlist.name,
        )
        for playlist in playlists
    ]
    if include_blank:
        return [(0, "No Playlist")] + choices
    return choices


def load_board_template_choices(include_blank: bool = True):
    """Return board template options for selection fields."""

    templates = BoardTemplate.query.order_by(BoardTemplate.name).all()
    choices = [
        (
            template.id,
            f"{template.name} (archived)" if template.archived else template.name,
        )
        for template in templates
    ]
    if include_blank:
        return [(0, "Use Display Defaults")] + choices
    return choices


def load_signage_media_asset_choices(
    include_blank: bool = True,
    *,
    media_type: str | None = None,
):
    """Return signage media asset options for selection fields."""

    query = SignageMediaAsset.query.order_by(SignageMediaAsset.created_at.desc())
    if media_type in {SignageMediaAsset.TYPE_IMAGE, SignageMediaAsset.TYPE_VIDEO}:
        query = query.filter_by(media_type=media_type)
    assets = query.all()
    choices = [
        (
            asset.id,
            (
                f"{asset.display_name} ({asset.media_type})"
                if asset.name and asset.name != asset.original_filename
                else asset.display_name
            ),
        )
        for asset in assets
    ]
    if include_blank:
        return [(0, "Use External URL")] + choices
    return choices


def load_permission_group_choices(include_system: bool = True):
    """Return available permission group choices."""
    query = PermissionGroup.query.order_by(
        PermissionGroup.is_system.desc(), PermissionGroup.name
    )
    if not include_system:
        query = query.filter_by(is_system=False)
    return [(group.id, group.name) for group in query.all()]


def load_schedule_department_choices(include_inactive: bool = False):
    query = Department.query.order_by(Department.name)
    if not include_inactive:
        query = query.filter_by(active=True)
    return [(department.id, department.name) for department in query.all()]


def load_schedule_position_choices(
    *,
    department_id: int | None = None,
    include_inactive: bool = False,
):
    query = ShiftPosition.query.options(selectinload(ShiftPosition.department)).order_by(
        ShiftPosition.sort_order, ShiftPosition.name
    )
    if department_id:
        query = query.filter_by(department_id=department_id)
    if not include_inactive:
        query = query.filter_by(active=True)
    return [
        (
            position.id,
            f"{position.department.name} - {position.name}"
            if position.department
            else position.name,
        )
        for position in query.all()
    ]


def load_active_user_choices():
    users = User.query.filter_by(active=True).all()
    users = sorted(users, key=lambda user: (user.sort_key, user.email.casefold()))
    return [(user.id, user.display_label) for user in users]


def load_location_choices(include_blank: bool = True):
    """Return location choices for selection fields."""

    locations = Location.query.order_by(Location.name).all()
    choices = [
        (location.id, f"{location.name} (archived)" if location.archived else location.name)
        for location in locations
    ]
    if include_blank:
        return [(0, "No Location")] + choices
    return choices


def load_vendor_choices(include_blank: bool = True):
    """Return vendor choices for selection fields."""

    vendors = Vendor.query.order_by(Vendor.first_name, Vendor.last_name).all()
    choices = [
        (
            vendor.id,
            (
                f"{vendor.first_name} {vendor.last_name}".strip()
                + (" (archived)" if vendor.archived else "")
            ).strip(),
        )
        for vendor in vendors
    ]
    if include_blank:
        return [(0, "No Vendor")] + choices
    return choices


def load_equipment_category_choices(include_blank: bool = True):
    """Return equipment category choices for selection fields."""

    categories = EquipmentCategory.query.order_by(EquipmentCategory.name).all()
    choices = [
        (
            category.id,
            f"{category.name} (archived)" if category.archived else category.name,
        )
        for category in categories
    ]
    if include_blank:
        return [(0, "No Category")] + choices
    return choices


def load_equipment_model_choices(include_blank: bool = True):
    """Return equipment model choices for selection fields."""

    models = (
        EquipmentModel.query.options(selectinload(EquipmentModel.category))
        .order_by(
            EquipmentModel.manufacturer.asc(),
            EquipmentModel.name.asc(),
            EquipmentModel.model_number.asc(),
        )
        .all()
    )
    choices = [
        (
            equipment_model.id,
            (
                f"{equipment_model.category.name if equipment_model.category else 'Uncategorized'}"
                f" - {equipment_model.display_name}"
                f"{' (archived)' if equipment_model.archived else ''}"
            ),
        )
        for equipment_model in models
    ]
    if include_blank:
        return [(0, "No Model")] + choices
    return choices


def load_equipment_asset_choices(include_blank: bool = True):
    """Return equipment asset choices for selection fields."""

    assets = (
        EquipmentAsset.query.options(
            selectinload(EquipmentAsset.equipment_model).selectinload(
                EquipmentModel.category
            )
        )
        .order_by(EquipmentAsset.asset_tag.asc())
        .all()
    )
    choices = [
        (
            asset.id,
            (
                f"{asset.asset_tag} - {asset.display_name}"
                f"{' (archived)' if asset.archived else ''}"
            ),
        )
        for asset in assets
    ]
    if include_blank:
        return [(0, "No Equipment")] + choices
    return choices


def load_purchase_order_choices(include_blank: bool = True):
    """Return purchase order choices for selection fields."""

    orders = (
        PurchaseOrder.query.options(selectinload(PurchaseOrder.vendor))
        .order_by(PurchaseOrder.order_date.desc(), PurchaseOrder.id.desc())
        .all()
    )
    choices = []
    for purchase_order in orders:
        vendor_label = ""
        if purchase_order.vendor is not None:
            vendor_label = (
                f"{purchase_order.vendor.first_name} {purchase_order.vendor.last_name}"
            ).strip()
        elif purchase_order.vendor_name:
            vendor_label = purchase_order.vendor_name
        order_reference = purchase_order.order_number or f"PO #{purchase_order.id}"
        suffix = f" - {vendor_label}" if vendor_label else ""
        status = f" ({purchase_order.status.title()})" if purchase_order.status else ""
        choices.append((purchase_order.id, f"{order_reference}{suffix}{status}"))
    if include_blank:
        return [(0, "No Purchase Order")] + choices
    return choices


def load_purchase_invoice_choices(include_blank: bool = True):
    """Return purchase invoice choices for selection fields."""

    invoices = (
        PurchaseInvoice.query.options(
            selectinload(PurchaseInvoice.purchase_order).selectinload(
                PurchaseOrder.vendor
            )
        )
        .order_by(PurchaseInvoice.received_date.desc(), PurchaseInvoice.id.desc())
        .all()
    )
    choices = []
    for invoice in invoices:
        invoice_reference = invoice.invoice_number or f"Invoice #{invoice.id}"
        vendor_label = ""
        purchase_order = invoice.purchase_order
        if purchase_order is not None and purchase_order.vendor is not None:
            vendor_label = (
                f"{purchase_order.vendor.first_name} {purchase_order.vendor.last_name}"
            ).strip()
        elif invoice.vendor_name:
            vendor_label = invoice.vendor_name
        po_reference = ""
        if purchase_order is not None:
            po_reference = purchase_order.order_number or f"PO #{purchase_order.id}"
        detail_bits = [invoice_reference]
        if po_reference:
            detail_bits.append(po_reference)
        if vendor_label:
            detail_bits.append(vendor_label)
        choices.append((invoice.id, " - ".join(detail_bits)))
    if include_blank:
        return [(0, "No Purchase Invoice")] + choices
    return choices


def load_schedule_membership_role_suggestions() -> list[str]:
    defaults = list(UserDepartmentMembership.DEFAULT_ROLE_SUGGESTIONS)
    existing_roles = [
        UserDepartmentMembership.normalize_role(role)
        for role, in UserDepartmentMembership.query.with_entities(
            UserDepartmentMembership.role
        )
        .distinct()
        .order_by(UserDepartmentMembership.role.asc())
        .all()
        if role
    ]
    suggestions = {
        role
        for role in defaults + existing_roles
        if role
    }
    return sorted(suggestions)


def load_purchase_gl_code_choices():
    """Return purchase GL code options filtered for expense accounts."""
    if "purchase_gl_code_choices" not in g:
        codes = (
            GLCode.query.filter(
                or_(GLCode.code.like("5%"), GLCode.code.like("6%"))
            )
            .order_by(GLCode.code)
            .all()
        )
        g.purchase_gl_code_choices = [
            (0, "Use Default GL Code")
        ] + [
            (
                code.id,
                f"{code.code} - {code.description}" if code.description else code.code,
            )
            for code in codes
        ]
    return g.purchase_gl_code_choices


def load_expense_gl_code_choices(include_unassigned: bool = False):
    """Return GL code choices limited to expense accounts."""
    if "expense_gl_code_choices" not in g:
        codes = (
            GLCode.query.filter(
                or_(GLCode.code.like("5%"), GLCode.code.like("6%"))
            )
            .order_by(GLCode.code)
            .all()
        )
        g.expense_gl_code_choices = [
            (
                code.id,
                f"{code.code} - {code.description}" if code.description else code.code,
            )
            for code in codes
        ]
    choices = list(g.expense_gl_code_choices)
    if include_unassigned:
        choices.append((-1, "Unassigned GL Code"))
    return choices


def load_sales_gl_code_choices(include_unassigned: bool = False):
    """Return GL code choices limited to sales accounts."""
    if "sales_gl_code_choices" not in g:
        codes = (
            GLCode.query.filter(GLCode.code.like("4%"))
            .order_by(GLCode.code)
            .all()
        )
        g.sales_gl_code_choices = [
            (
                code.id,
                f"{code.code} - {code.description}" if code.description else code.code,
            )
            for code in codes
        ]
    choices = list(g.sales_gl_code_choices)
    if include_unassigned:
        choices.append((-1, "Unassigned Sales GL Code"))
    return choices


@lru_cache(maxsize=1)
def get_timezone_choices():
    """Return a sorted list of available time zones.

    The list is computed only once and cached for subsequent calls to
    avoid the cost of generating it at import time.
    """
    return sorted(available_timezones())


class LoginForm(FlaskForm):
    email = StringField("Email", validators=[DataRequired(), Email()])
    password = PasswordField("Password", validators=[DataRequired()])
    remember = BooleanField("Remember me", default=False)


class CSRFOnlyForm(FlaskForm):
    """Simple form that only provides CSRF protection."""

    pass


class PasswordResetRequestForm(FlaskForm):
    """Form for requesting a password reset email."""

    email = StringField("Email", validators=[DataRequired(), Email()])
    submit = SubmitField("Send Reset Email")


class LocationForm(FlaskForm):
    name = StringField(
        "Location Name", validators=[DataRequired(), Length(min=2, max=100)]
    )
    menu_id = SelectField(
        "Menu",
        coerce=int,
        validators=[Optional()],
        validate_choice=False,
        default=0,
    )
    default_playlist_id = SelectField(
        "Default Playlist",
        coerce=int,
        validators=[Optional()],
        validate_choice=False,
        default=0,
    )
    is_spoilage = BooleanField("Spoilage Location")
    submit = SubmitField("Submit")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.menu_id.choices = load_menu_choices()
        self.default_playlist_id.choices = load_playlist_choices()
        if self.menu_id.data is None:
            self.menu_id.data = 0
        if self.default_playlist_id.data is None:
            self.default_playlist_id.data = 0


class LocationItemAddForm(FlaskForm):
    """Form used to add standalone items to a location."""

    item_id = SelectField(
        "Item", coerce=int, validators=[DataRequired()], validate_choice=False
    )
    expected_count = DecimalField(
        "Expected Count", validators=[Optional()], places=None, default=0
    )
    submit = SubmitField("Add Item")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Default choices are populated with all active items. Views using this
        # form typically override ``item_id.choices`` to remove already-added
        # items, but setting the base list here ensures the field works when the
        # view does not provide its own list.
        self.item_id.choices = load_item_choices()


class BulkLocationUpdateForm(FlaskForm):
    """Form used to apply bulk updates to locations."""

    selected_ids = HiddenField(validators=[DataRequired()])
    apply_name = BooleanField("Apply")
    name = StringField(
        "Location Name", validators=[Optional(), Length(min=2, max=100)]
    )
    apply_menu_id = BooleanField("Apply")
    menu_id = SelectField(
        "Menu", coerce=int, validators=[Optional()], validate_choice=False
    )
    apply_is_spoilage = BooleanField("Apply")
    is_spoilage = BooleanField("Spoilage Location")
    apply_archived = BooleanField("Apply")
    archived = BooleanField("Archived")
    submit = SubmitField("Apply Updates")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.menu_id.choices = [(0, "No Menu")] + load_menu_choices(
            include_blank=False
        )

    def validate(self, **kwargs):
        valid = super().validate(**kwargs)
        if not valid:
            return False

        apply_fields = [
            self.apply_name.data,
            self.apply_menu_id.data,
            self.apply_is_spoilage.data,
            self.apply_archived.data,
        ]
        if not any(apply_fields):
            self.form_errors.append("Select at least one field to update.")
            return False

        if self.apply_name.data and not self.name.data:
            self.name.errors.append("Enter a name to apply.")
            return False

        if self.apply_menu_id.data and self.menu_id.data is None:
            self.menu_id.errors.append("Select a menu.")
            return False

        return True


class BulkItemUpdateForm(FlaskForm):
    """Form used to apply bulk updates to inventory items."""

    selected_ids = HiddenField(validators=[DataRequired()])
    apply_name = BooleanField("Apply")
    name = StringField("Name", validators=[Optional(), Length(max=100)])
    apply_base_unit = BooleanField("Apply")
    base_unit = SelectField(
        "Base Unit",
        choices=BASE_UNIT_CHOICES,
        validators=[Optional()],
        validate_choice=False,
    )
    apply_gl_code_id = BooleanField("Apply")
    gl_code_id = SelectField(
        "Inventory GL Code",
        coerce=int,
        validators=[Optional()],
        validate_choice=False,
    )
    apply_purchase_gl_code_id = BooleanField("Apply")
    purchase_gl_code_id = SelectField(
        "Purchase GL Code",
        coerce=int,
        validators=[Optional()],
        validate_choice=False,
    )
    apply_archived = BooleanField("Apply")
    archived = BooleanField("Archived")
    submit = SubmitField("Apply Updates")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        purchase_codes = ItemForm._fetch_purchase_gl_codes()
        formatted = [
            (
                code.id,
                f"{code.code} - {code.description}" if code.description else code.code,
            )
            for code in purchase_codes
        ]
        self.gl_code_id.choices = [(0, "Unassigned")] + formatted
        self.purchase_gl_code_id.choices = load_purchase_gl_code_choices()

    def validate(self, **kwargs):
        valid = super().validate(**kwargs)
        if not valid:
            return False

        apply_fields = [
            self.apply_name.data,
            self.apply_base_unit.data,
            self.apply_gl_code_id.data,
            self.apply_purchase_gl_code_id.data,
            self.apply_archived.data,
        ]
        if not any(apply_fields):
            self.form_errors.append("Select at least one field to update.")
            return False

        if self.apply_name.data and not self.name.data:
            self.name.errors.append("Enter a name to apply.")
            return False

        if self.apply_gl_code_id.data and self.gl_code_id.data is None:
            self.gl_code_id.errors.append("Select an inventory GL code.")
            return False

        if (
            self.apply_purchase_gl_code_id.data
            and self.purchase_gl_code_id.data is None
        ):
            self.purchase_gl_code_id.errors.append("Select a purchase GL code.")
            return False

        return True


class ItemUnitForm(FlaskForm):
    name = StringField("Unit Name", validators=[DataRequired()])
    factor = DecimalField("Factor", validators=[InputRequired()])
    receiving_default = BooleanField("Receiving Default")
    transfer_default = BooleanField("Transfer Default")


class ItemBarcodeForm(FlaskForm):
    code = StringField("Barcode", validators=[Optional(), Length(max=32)])


class ItemForm(FlaskForm):
    name = StringField("Name", validators=[DataRequired()])
    gl_code = SelectField("GL Code", validators=[Optional()])
    base_unit = SelectField(
        "Base Unit", choices=BASE_UNIT_CHOICES, validators=[DataRequired()]
    )
    gl_code_id = SelectField(
        "GL Code", coerce=int, validators=[Optional()], validate_choice=False
    )
    purchase_gl_code = SelectField(
        "Purchase GL Code", coerce=int, validators=[Optional()]
    )
    barcodes = FieldList(FormField(ItemBarcodeForm), min_entries=1)
    units = FieldList(FormField(ItemUnitForm), min_entries=1)
    submit = SubmitField("Submit")

    def __init__(self, *args, **kwargs):
        super(ItemForm, self).__init__(*args, **kwargs)
        codes = self._fetch_purchase_gl_codes()
        self.gl_code.choices = [
            (
                g.code,
                f"{g.code} - {g.description}" if g.description else g.code,
            )
            for g in codes
        ]
        purchase_codes = [
            (g.id, f"{g.code} - {g.description}" if g.description else g.code)
            for g in codes
        ]
        self.gl_code_id.choices = purchase_codes
        self.purchase_gl_code.choices = purchase_codes

    def validate_gl_code(self, field):
        if field.data and not str(field.data).startswith(("5", "6")):
            raise ValidationError("Item GL codes must start with 5 or 6")
        codes = self._fetch_purchase_gl_codes()
        purchase_codes = [
            (g.id, f"{g.code} - {g.description}" if g.description else g.code)
            for g in codes
        ]
        self.gl_code_id.choices = purchase_codes

    @staticmethod
    def _fetch_purchase_gl_codes():
        return (
            GLCode.query.filter(
                or_(GLCode.code.like("5%"), GLCode.code.like("6%"))
            )
            .order_by(GLCode.code)
            .all()
        )


class MenuForm(FlaskForm):
    name = StringField("Menu Name", validators=[DataRequired(), Length(max=100)])
    description = TextAreaField("Description", validators=[Optional()])
    product_ids = SelectMultipleField(
        "Products", coerce=int, validators=[Optional()], validate_choice=False
    )
    submit = SubmitField("Save Menu")

    def validate_name(self, field):
        query = Menu.query.filter(Menu.name == field.data)
        if self.obj_id is not None:
            query = query.filter(Menu.id != self.obj_id)
        if query.first() is not None:
            raise ValidationError("A menu with this name already exists.")

    def __init__(self, *args, **kwargs):
        self.obj_id = kwargs.pop("obj_id", None)
        super().__init__(*args, **kwargs)
        self.product_ids.choices = load_product_choices()


class MenuAssignmentForm(FlaskForm):
    location_ids = SelectMultipleField(
        "Locations", coerce=int, validators=[Optional()], validate_choice=False
    )
    submit = SubmitField("Assign Menu")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.location_ids.choices = [
            (loc.id, f"{loc.name} (archived)" if loc.archived else loc.name)
            for loc in Location.query.order_by(Location.name).all()
        ]


class PlaylistItemForm(FlaskForm):
    source_type = SelectField(
        "Menu Source",
        choices=[
            (PlaylistItem.SOURCE_LOCATION_MENU, "Use location menu"),
            (PlaylistItem.SOURCE_MENU, "Use specific menu"),
        ],
        validators=[DataRequired()],
    )
    menu_id = SelectField(
        "Menu",
        coerce=int,
        validators=[Optional()],
        validate_choice=False,
        default=0,
    )
    duration_seconds = IntegerField(
        "Duration (seconds)",
        validators=[InputRequired(), NumberRange(min=5, max=3600)],
        default=15,
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.menu_id.choices = load_menu_choices()
        if self.menu_id.data is None:
            self.menu_id.data = 0


class PlaylistForm(FlaskForm):
    name = StringField("Playlist Name", validators=[DataRequired(), Length(max=100)])
    description = TextAreaField("Description", validators=[Optional()])
    items = FieldList(FormField(PlaylistItemForm), min_entries=0)
    submit = SubmitField("Save Playlist")

    def __init__(self, *args, **kwargs):
        self.obj_id = kwargs.pop("obj_id", None)
        super().__init__(*args, **kwargs)
        for item_form in self.items:
            item_form.source_type.choices = PlaylistItemForm().source_type.choices
            item_form.menu_id.choices = load_menu_choices()

    def validate_name(self, field):
        query = Playlist.query.filter(Playlist.name == field.data)
        if self.obj_id is not None:
            query = query.filter(Playlist.id != self.obj_id)
        if query.first() is not None:
            raise ValidationError("A playlist with this name already exists.")

    def validate(self, **kwargs):
        valid = super().validate(**kwargs)
        if not valid:
            return False
        if not self.items.entries:
            self.form_errors.append("Add at least one playlist item.")
            return False
        for item_form in self.items:
            source_type = item_form.source_type.data
            if (
                source_type == PlaylistItem.SOURCE_MENU
                and not int(item_form.menu_id.data or 0)
            ):
                item_form.menu_id.errors.append("Select a menu for this playlist item.")
                return False
        return True


class DisplayForm(FlaskForm):
    name = StringField("Display Name", validators=[DataRequired(), Length(max=100)])
    location_id = SelectField(
        "Location",
        coerce=int,
        validators=[DataRequired()],
        validate_choice=False,
    )
    playlist_override_id = SelectField(
        "Playlist Override",
        coerce=int,
        validators=[Optional()],
        validate_choice=False,
        default=0,
    )
    board_template_id = SelectField(
        "Board Template",
        coerce=int,
        validators=[Optional()],
        validate_choice=False,
        default=0,
    )
    board_columns = IntegerField(
        "Board Columns",
        validators=[InputRequired(), NumberRange(min=1, max=6)],
        default=3,
    )
    board_rows = IntegerField(
        "Rows Per Page",
        validators=[InputRequired(), NumberRange(min=1, max=8)],
        default=4,
    )
    selected_product_ids = SelectMultipleField(
        "Visible Products",
        coerce=int,
        validators=[Optional()],
        validate_choice=False,
    )
    show_prices = BooleanField("Show Prices", default=True)
    show_menu_description = BooleanField("Show Menu Description")
    archived = BooleanField("Archived")
    submit = SubmitField("Save Display")

    def __init__(self, *args, **kwargs):
        self.obj_id = kwargs.pop("obj_id", None)
        super().__init__(*args, **kwargs)
        self.location_id.choices = [
            (location.id, f"{location.name} (archived)" if location.archived else location.name)
            for location in Location.query.order_by(Location.name).all()
        ]
        self.playlist_override_id.choices = load_playlist_choices()
        if self.playlist_override_id.data is None:
            self.playlist_override_id.data = 0
        self.board_template_id.choices = load_board_template_choices()
        if self.board_template_id.data is None:
            self.board_template_id.data = 0
        self.selected_product_ids.choices = load_location_menu_product_choices(
            self.location_id.data
        )
        if self.selected_product_ids.data is None:
            self.selected_product_ids.data = []

    def validate_name(self, field):
        query = Display.query.filter(
            Display.name == field.data,
            Display.location_id == self.location_id.data,
        )
        if self.obj_id is not None:
            query = query.filter(Display.id != self.obj_id)
        if query.first() is not None:
            raise ValidationError(
                "A display with this name already exists for the selected location."
            )


class BoardTemplateBlockForm(FlaskForm):
    BLOCK_TYPE_CHOICES = [
        (BoardTemplateBlock.TYPE_MENU, "Menu Block"),
        (BoardTemplateBlock.TYPE_TEXT, "Text Block"),
        (BoardTemplateBlock.TYPE_IMAGE, "Image Block"),
        (BoardTemplateBlock.TYPE_VIDEO, "Video Block"),
    ]

    block_type = SelectField(
        "Block Type",
        validators=[DataRequired()],
        choices=BLOCK_TYPE_CHOICES,
    )
    width_units = IntegerField(
        "Width Units",
        validators=[InputRequired(), NumberRange(min=1, max=12)],
        default=6,
    )
    grid_x = HiddenField("Grid X", default="1")
    grid_y = HiddenField("Grid Y", default="1")
    grid_width = HiddenField("Grid Width", default="12")
    grid_height = HiddenField("Grid Height", default="10")
    title = StringField("Title", validators=[Optional(), Length(max=120)])
    body = TextAreaField("Text", validators=[Optional()])
    media_asset_id = SelectField(
        "Media Library Asset",
        coerce=int,
        validators=[Optional()],
        validate_choice=False,
        default=0,
    )
    media_url = StringField("Media URL", validators=[Optional(), Length(max=500)])
    menu_columns = IntegerField(
        "Menu Columns",
        validators=[InputRequired(), NumberRange(min=1, max=6)],
        default=2,
    )
    menu_rows = IntegerField(
        "Rows Per Page",
        validators=[InputRequired(), NumberRange(min=1, max=8)],
        default=4,
    )
    selected_product_ids = SelectMultipleField(
        "Menu Products",
        coerce=int,
        validators=[Optional()],
        validate_choice=False,
    )
    show_title = BooleanField("Show Title", default=True)
    show_prices = BooleanField("Show Prices", default=True)
    show_menu_description = BooleanField("Show Menu Description")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.selected_product_ids.choices = load_product_choices()
        if self.selected_product_ids.data is None:
            self.selected_product_ids.data = []
        self.media_asset_id.choices = load_signage_media_asset_choices()
        if self.media_asset_id.data is None:
            self.media_asset_id.data = 0


class SignageMediaUploadForm(FlaskForm):
    name = StringField("Asset Name", validators=[Optional(), Length(max=120)])
    file = FileField(
        "Media File",
        validators=[
            FileRequired(),
            FileAllowed(
                {
                    "jpg",
                    "jpeg",
                    "png",
                    "gif",
                    "webp",
                    "svg",
                    "mp4",
                    "webm",
                    "mov",
                    "m4v",
                },
                "Only image or video files are allowed.",
            ),
        ],
    )
    submit = SubmitField("Upload Asset")


class BoardTemplateForm(FlaskForm):
    THEME_CHOICES = [
        (BoardTemplate.THEME_AURORA, "Aurora Blue"),
        (BoardTemplate.THEME_MIDNIGHT, "Midnight Slate"),
        (BoardTemplate.THEME_SUNSET, "Sunset Amber"),
        (BoardTemplate.THEME_CONCOURSE, "Concourse Gold"),
    ]
    PANEL_CHOICES = [
        (BoardTemplate.PANEL_NONE, "No Side Panel"),
        (BoardTemplate.PANEL_LEFT, "Left Side Panel"),
        (BoardTemplate.PANEL_RIGHT, "Right Side Panel"),
    ]

    name = StringField("Template Name", validators=[DataRequired(), Length(max=100)])
    description = TextAreaField("Description", validators=[Optional()])
    canvas_width = IntegerField(
        "Canvas Width",
        validators=[InputRequired(), NumberRange(min=640, max=7680)],
        default=1920,
    )
    canvas_height = IntegerField(
        "Canvas Height",
        validators=[InputRequired(), NumberRange(min=360, max=4320)],
        default=1080,
    )
    theme = SelectField("Theme", validators=[DataRequired()], choices=THEME_CHOICES)
    brand_label = StringField("Brand Label", validators=[Optional(), Length(max=80)])
    brand_name = StringField("Brand Name", validators=[Optional(), Length(max=120)])
    menu_columns = IntegerField(
        "Menu Columns",
        validators=[InputRequired(), NumberRange(min=1, max=6)],
        default=3,
    )
    menu_rows = IntegerField(
        "Rows Per Page",
        validators=[InputRequired(), NumberRange(min=1, max=8)],
        default=4,
    )
    side_panel_position = SelectField(
        "Side Panel",
        validators=[DataRequired()],
        choices=PANEL_CHOICES,
    )
    side_panel_width_percent = IntegerField(
        "Side Panel Width (%)",
        validators=[InputRequired(), NumberRange(min=20, max=45)],
        default=30,
    )
    side_title = StringField("Side Panel Title", validators=[Optional(), Length(max=120)])
    side_body = TextAreaField("Side Panel Text", validators=[Optional()])
    side_image_url = StringField(
        "Side Panel Image URL", validators=[Optional(), Length(max=500)]
    )
    footer_text = StringField("Footer Text", validators=[Optional(), Length(max=255)])
    show_prices = BooleanField("Show Prices", default=True)
    show_menu_description = BooleanField("Show Menu Description")
    show_page_indicator = BooleanField("Show Page Indicator", default=True)
    blocks = FieldList(FormField(BoardTemplateBlockForm), min_entries=0)
    archived = BooleanField("Archived")
    submit = SubmitField("Save Template")

    def __init__(self, *args, **kwargs):
        self.obj_id = kwargs.pop("obj_id", None)
        super().__init__(*args, **kwargs)

    def validate_name(self, field):
        query = BoardTemplate.query.filter(BoardTemplate.name == field.data)
        if self.obj_id is not None:
            query = query.filter(BoardTemplate.id != self.obj_id)
        if query.first() is not None:
            raise ValidationError("A board template with this name already exists.")

    def validate(self, **kwargs):
        valid = super().validate(**kwargs)
        if not valid:
            return False

        if len(self.blocks.entries) > 6:
            self.form_errors.append("Use at most six blocks in one template.")
            return False

        for block_form in self.blocks.entries:
            block_type = block_form.block_type.data
            try:
                grid_x = int(block_form.grid_x.data or 0)
                grid_y = int(block_form.grid_y.data or 0)
                grid_width = int(block_form.grid_width.data or 0)
                grid_height = int(block_form.grid_height.data or 0)
            except (TypeError, ValueError):
                self.form_errors.append(
                    "Each block needs a valid position and size on the board."
                )
                return False

            if (
                grid_x < 1
                or grid_y < 1
                or grid_width < 1
                or grid_height < 1
                or grid_x + grid_width - 1 > BoardTemplate.GRID_COLUMNS
                or grid_y + grid_height - 1 > BoardTemplate.GRID_ROWS
            ):
                self.form_errors.append(
                    "Blocks must stay within the board canvas."
                )
                return False

            width_units = max(
                1,
                min(12, int(round(float(grid_width) / 2.0))),
            )
            block_form.width_units.data = width_units

            media_asset_id = int(block_form.media_asset_id.data or 0)
            asset = None
            if media_asset_id:
                asset = db.session.get(SignageMediaAsset, media_asset_id)
                if asset is None:
                    block_form.media_asset_id.errors.append(
                        "Selected media asset is no longer available."
                    )
                    return False

            if block_type in (
                BoardTemplateBlock.TYPE_IMAGE,
                BoardTemplateBlock.TYPE_VIDEO,
            ):
                if not media_asset_id and not (block_form.media_url.data or "").strip():
                    block_form.media_url.errors.append(
                        "Choose a media asset or provide an external media URL."
                    )
                    return False
                if asset is not None and asset.media_type != block_type:
                    block_form.media_asset_id.errors.append(
                        "The selected asset type does not match this block type."
                    )
                    return False

            if block_type == BoardTemplateBlock.TYPE_TEXT and not (
                (block_form.title.data or "").strip()
                or (block_form.body.data or "").strip()
            ):
                block_form.body.errors.append(
                    "Text blocks need a title or body."
                )
                return False

        return True


class PurchaseCostForecastForm(FlaskForm):
    """Form used to generate purchase cost forecast reports."""

    forecast_period = SelectField(
        "Forecast Period",
        coerce=int,
        choices=[
            (7, "7 Days"),
            (14, "14 Days"),
            (30, "30 Days"),
            (60, "60 Days"),
            (90, "90 Days"),
            (182, "6 Months"),
            (365, "1 Year"),
        ],
        validators=[DataRequired()],
    )
    history_window = SelectField(
        "History Window",
        coerce=int,
        choices=[
            (30, "30 Days"),
            (60, "60 Days"),
            (90, "90 Days"),
            (182, "6 Months"),
            (365, "1 Year"),
        ],
        default=30,
        validators=[DataRequired()],
    )
    location_id = SelectField(
        "Location",
        coerce=int,
        validators=[Optional()],
        default=0,
    )
    purchase_gl_code_ids = SelectMultipleField(
        "Purchase GL Codes",
        coerce=int,
        validators=[Optional()],
        validate_choice=False,
        default=[0],
    )
    item_id = SelectField(
        "Item",
        coerce=int,
        validators=[Optional()],
        default=0,
    )
    submit = SubmitField("Generate Forecast")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        location_choices = [(0, "All Locations")]
        location_choices.extend(
            (
                loc.id,
                loc.name,
            )
            for loc in Location.query.filter_by(archived=False)
            .order_by(Location.name)
            .all()
        )
        self.location_id.choices = location_choices

        item_choices = [(0, "All Items")]
        item_choices.extend(load_item_choices())
        self.item_id.choices = item_choices

        gl_code_choices = [(0, "All Purchase GL Codes")]
        gl_code_choices.extend(
            (
                code.id,
                f"{code.code} - {code.description}" if code.description else code.code,
            )
            for code in GLCode.query.filter(
                or_(GLCode.code.like("5%"), GLCode.code.like("6%"))
            ).order_by(GLCode.code)
        )
        self.purchase_gl_code_ids.choices = gl_code_choices

        if not self.purchase_gl_code_ids.data:
            self.purchase_gl_code_ids.data = [0]


class TransferItemForm(FlaskForm):
    class Meta:
        csrf = False

    item = SelectField("Item", coerce=int)
    unit = SelectField(
        "Unit", coerce=int, validators=[Optional()], validate_choice=False
    )
    quantity = DecimalField("Quantity", validators=[Optional()])


class TransferForm(FlaskForm):
    # Your existing fields
    from_location_id = SelectField(
        "From Location", coerce=int, validators=[DataRequired()]
    )
    to_location_id = SelectField(
        "To Location", coerce=int, validators=[DataRequired()]
    )
    items = FieldList(FormField(TransferItemForm), min_entries=1)
    submit = SubmitField("Transfer")

    def __init__(self, *args, **kwargs):
        super(TransferForm, self).__init__(*args, **kwargs)
        # Dynamically set choices for from_location_id and to_location_id
        locations = [
            (loc.id, loc.name)
            for loc in Location.query.filter_by(archived=False).all()
        ]
        self.from_location_id.choices = locations
        self.to_location_id.choices = locations
        items = load_item_choices()
        for item_form in self.items:
            item_form.item.choices = items
            item_form.unit.choices = []


class UserForm(FlaskForm):
    pass


class InviteUserForm(FlaskForm):
    email = StringField("Email", validators=[DataRequired(), Email()])
    display_name = StringField(
        "Display Name",
        validators=[Optional(), Length(max=120)],
    )
    group_ids = SelectMultipleField(
        "Permission Groups",
        coerce=int,
        validators=[Optional()],
        render_kw={"size": 8},
    )
    submit = SubmitField("Send Invite")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.group_ids.choices = load_permission_group_choices()


class UserAccessForm(FlaskForm):
    display_name = StringField(
        "Display Name",
        validators=[Optional(), Length(max=120)],
    )
    group_ids = SelectMultipleField(
        "Permission Groups",
        coerce=int,
        validators=[Optional()],
        render_kw={"size": 10},
    )
    submit = SubmitField("Save Access")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.group_ids.choices = load_permission_group_choices()


class PermissionGroupForm(FlaskForm):
    name = StringField("Group Name", validators=[DataRequired(), Length(max=100)])
    description = TextAreaField(
        "Description", validators=[Optional(), Length(max=1000)]
    )
    inherited_group_ids = SelectMultipleField(
        "Copy Permissions From Existing Groups",
        coerce=int,
        validators=[Optional()],
        render_kw={"size": 8},
    )
    permissions = SelectMultipleField(
        "Permissions",
        coerce=str,
        validators=[Optional()],
        validate_choice=False,
    )
    submit = SubmitField("Save Group")

    def __init__(self, *args, exclude_group_id=None, **kwargs):
        super().__init__(*args, **kwargs)
        from app.permissions import PERMISSION_DEFINITIONS

        group_choices = load_permission_group_choices()
        if exclude_group_id is not None:
            group_choices = [
                (group_id, group_name)
                for group_id, group_name in group_choices
                if int(group_id) != int(exclude_group_id)
            ]
        self.inherited_group_ids.choices = group_choices
        self.permissions.choices = [
            (definition.code, f"{definition.label} ({definition.code})")
            for definition in PERMISSION_DEFINITIONS
        ]


class PermissionAssignmentForm(FlaskForm):
    permissions = SelectMultipleField(
        "Permissions",
        coerce=str,
        validators=[Optional()],
        render_kw={"size": 20},
    )
    submit = SubmitField("Save Permissions")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from app.permissions import PERMISSION_DEFINITIONS

        self.permissions.choices = [
            (definition.code, f"{definition.label} ({definition.code})")
            for definition in PERMISSION_DEFINITIONS
        ]


class ChangePasswordForm(FlaskForm):
    current_password = PasswordField(
        "Current Password", validators=[DataRequired()]
    )
    new_password = PasswordField("New Password", validators=[DataRequired()])
    confirm_password = PasswordField(
        "Confirm Password",
        validators=[
            DataRequired(),
            EqualTo("new_password", message="Passwords must match"),
        ],
    )
    submit = SubmitField("Change Password")


class SetPasswordForm(FlaskForm):
    new_password = PasswordField("New Password", validators=[DataRequired()])
    confirm_password = PasswordField(
        "Confirm Password",
        validators=[
            DataRequired(),
            EqualTo("new_password", message="Passwords must match"),
        ],
    )
    submit = SubmitField("Set Password")


class ImportItemsForm(FlaskForm):
    file = FileField("Item File", validators=[FileRequired()])
    submit = SubmitField("Import")


class DateRangeForm(FlaskForm):
    start_datetime = DateTimeLocalField(
        "Start Date/Time",
        format="%Y-%m-%d %H:%M",
        validators=[DataRequired()],
        id="start_datetime",
    )
    end_datetime = DateTimeLocalField(
        "End Date/Time",
        format="%Y-%m-%d %H:%M",
        validators=[DataRequired()],
        id="end_datetime",
    )
    from_location_ids = SelectMultipleField(
        "From Locations",
        coerce=int,
        validators=[Optional()],
        validate_choice=False,
    )
    to_location_ids = SelectMultipleField(
        "To Locations",
        coerce=int,
        validators=[Optional()],
        validate_choice=False,
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        locations = (
            Location.query.filter(Location.archived.is_(False))
            .order_by(Location.name)
            .all()
        )
        choices = [(location.id, location.name) for location in locations]
        self.from_location_ids.choices = choices
        self.to_location_ids.choices = choices


class SpoilageFilterForm(FlaskForm):
    start_date = DateField("Start Date", validators=[Optional()])
    end_date = DateField("End Date", validators=[Optional()])
    purchase_gl_code = SelectField(
        "Purchase GL Code",
        coerce=int,
        validators=[Optional()],
        validate_choice=False,
    )
    items = SelectMultipleField(
        "Items", coerce=int, validators=[Optional()], validate_choice=False
    )
    submit = SubmitField("Filter")

    def __init__(self, *args, **kwargs):
        super(SpoilageFilterForm, self).__init__(*args, **kwargs)
        gl_codes = ItemForm._fetch_purchase_gl_codes()
        self.purchase_gl_code.choices = [(0, "All Purchase GL Codes")] + [
            (g.id, f"{g.code} - {g.description}" if g.description else g.code)
            for g in gl_codes
        ]
        self.items.choices = load_item_choices()


class CustomerForm(FlaskForm):
    first_name = StringField("First Name", validators=[DataRequired()])
    last_name = StringField("Last Name", validators=[DataRequired()])
    # These checkboxes represent whether GST/PST should be charged. The
    # underlying model stores exemption flags, so we invert these values in
    # the routes when saving/loading data.
    gst_exempt = BooleanField("Charge GST")
    pst_exempt = BooleanField("Charge PST")
    submit = SubmitField("Submit")


class EquipmentCategoryForm(FlaskForm):
    name = StringField(
        "Category Name",
        validators=[DataRequired(), Length(max=100)],
    )
    description = TextAreaField(
        "Description",
        validators=[Optional(), Length(max=2000)],
    )
    submit = SubmitField("Save Category")

    def __init__(self, *args, **kwargs):
        self.obj_id = kwargs.pop("obj_id", None)
        super().__init__(*args, **kwargs)

    def validate_name(self, field):
        normalized = (field.data or "").strip()
        if not normalized:
            raise ValidationError("Category name is required.")
        field.data = normalized
        query = EquipmentCategory.query.filter(
            func.lower(EquipmentCategory.name) == normalized.lower(),
            EquipmentCategory.archived.is_(False),
        )
        if self.obj_id is not None:
            query = query.filter(EquipmentCategory.id != self.obj_id)
        if query.first() is not None:
            raise ValidationError("An equipment category with this name already exists.")


class EquipmentModelForm(FlaskForm):
    category_id = SelectField(
        "Category",
        coerce=int,
        validators=[DataRequired()],
        validate_choice=False,
    )
    manufacturer = StringField(
        "Manufacturer",
        validators=[DataRequired(), Length(max=120)],
    )
    name = StringField(
        "Model Name",
        validators=[DataRequired(), Length(max=120)],
    )
    model_number = StringField(
        "Model Number",
        validators=[Optional(), Length(max=120)],
    )
    description = TextAreaField(
        "Description",
        validators=[Optional(), Length(max=2000)],
    )
    submit = SubmitField("Save Model")

    def __init__(self, *args, **kwargs):
        self.obj_id = kwargs.pop("obj_id", None)
        super().__init__(*args, **kwargs)
        self.category_id.choices = load_equipment_category_choices(include_blank=False)

    def validate_category_id(self, field):
        if db.session.get(EquipmentCategory, field.data) is None:
            raise ValidationError("Select a valid equipment category.")

    def validate(self, **kwargs):
        valid = super().validate(**kwargs)
        if not valid:
            return False

        manufacturer = (self.manufacturer.data or "").strip()
        name = (self.name.data or "").strip()
        model_number = (self.model_number.data or "").strip() or None

        if not manufacturer:
            self.manufacturer.errors.append("Manufacturer is required.")
            return False
        if not name:
            self.name.errors.append("Model name is required.")
            return False

        self.manufacturer.data = manufacturer
        self.name.data = name
        self.model_number.data = model_number

        query = EquipmentModel.query.filter(
            EquipmentModel.category_id == self.category_id.data,
            func.lower(EquipmentModel.manufacturer) == manufacturer.lower(),
            func.lower(EquipmentModel.name) == name.lower(),
            EquipmentModel.archived.is_(False),
        )
        if model_number:
            query = query.filter(
                func.lower(func.coalesce(EquipmentModel.model_number, ""))
                == model_number.lower()
            )
        else:
            query = query.filter(EquipmentModel.model_number.is_(None))
        if self.obj_id is not None:
            query = query.filter(EquipmentModel.id != self.obj_id)
        if query.first() is not None:
            self.name.errors.append(
                "An equipment model with this category, manufacturer, and model number already exists."
            )
            return False
        return True


class EquipmentAssetForm(FlaskForm):
    equipment_model_id = SelectField(
        "Equipment Model",
        coerce=int,
        validators=[DataRequired()],
        validate_choice=False,
    )
    name = StringField(
        "Equipment Name",
        validators=[Optional(), Length(max=120)],
    )
    asset_tag = StringField(
        "Asset Tag",
        validators=[DataRequired(), Length(max=64)],
    )
    serial_number = StringField(
        "Serial Number",
        validators=[Optional(), Length(max=128)],
    )
    status = SelectField(
        "Status",
        validators=[DataRequired()],
        choices=EquipmentAsset.STATUS_CHOICES,
    )
    description = TextAreaField(
        "Description",
        validators=[Optional(), Length(max=4000)],
    )
    acquired_on = FlexibleDateField("Acquired On", validators=[Optional()])
    warranty_expires_on = FlexibleDateField(
        "Warranty Expires On", validators=[Optional()]
    )
    cost = DecimalField(
        "Cost",
        validators=[Optional(), NumberRange(min=0)],
        places=2,
    )
    purchase_vendor_id = SelectField(
        "Purchased From",
        coerce=int,
        validators=[Optional()],
        validate_choice=False,
        default=0,
    )
    service_vendor_id = SelectField(
        "Service Vendor",
        coerce=int,
        validators=[Optional()],
        validate_choice=False,
        default=0,
    )
    service_contact_name = StringField(
        "Service Contact Name",
        validators=[Optional(), Length(max=120)],
    )
    service_contact_email = StringField(
        "Service Contact Email",
        validators=[Optional(), Email(), Length(max=255)],
    )
    service_contact_phone = StringField(
        "Service Contact Phone",
        validators=[Optional(), Length(max=50)],
    )
    service_contract_name = StringField(
        "Service Contract Name",
        validators=[Optional(), Length(max=120)],
    )
    service_contract_reference = StringField(
        "Service Contract Reference",
        validators=[Optional(), Length(max=120)],
    )
    service_contract_expires_on = FlexibleDateField(
        "Service Contract Expires On", validators=[Optional()]
    )
    service_interval_days = IntegerField(
        "Service Interval (days)",
        validators=[Optional(), NumberRange(min=1)],
    )
    last_service_on = FlexibleDateField("Last Service On", validators=[Optional()])
    next_service_due_on = FlexibleDateField(
        "Next Service Due On", validators=[Optional()]
    )
    service_contract_notes = TextAreaField(
        "Service Contract Notes",
        validators=[Optional(), Length(max=2000)],
    )
    location_id = SelectField(
        "Location",
        coerce=int,
        validators=[Optional()],
        validate_choice=False,
        default=0,
    )
    sublocation = StringField(
        "Sublocation / Room / Station",
        validators=[Optional(), Length(max=120)],
    )
    assigned_user_id = SelectField(
        "Current Custodian",
        coerce=int,
        validators=[Optional()],
        validate_choice=False,
        default=0,
    )
    submit = SubmitField("Save Equipment")

    def __init__(self, *args, **kwargs):
        self.obj_id = kwargs.pop("obj_id", None)
        super().__init__(*args, **kwargs)
        self.equipment_model_id.choices = load_equipment_model_choices(
            include_blank=False
        )
        self.purchase_vendor_id.choices = load_vendor_choices()
        self.service_vendor_id.choices = load_vendor_choices()
        self.location_id.choices = load_location_choices()
        self.assigned_user_id.choices = [(0, "No Custodian")] + load_active_user_choices()

    def validate_equipment_model_id(self, field):
        if db.session.get(EquipmentModel, field.data) is None:
            raise ValidationError("Select a valid equipment model.")

    def validate_asset_tag(self, field):
        normalized = (field.data or "").strip()
        if not normalized:
            raise ValidationError("Asset tag is required.")
        field.data = normalized
        query = EquipmentAsset.query.filter(
            func.lower(EquipmentAsset.asset_tag) == normalized.lower()
        )
        if self.obj_id is not None:
            query = query.filter(EquipmentAsset.id != self.obj_id)
        if query.first() is not None:
            raise ValidationError("This asset tag is already in use.")

    def validate_serial_number(self, field):
        normalized = (field.data or "").strip() or None
        field.data = normalized
        if not normalized:
            return
        query = EquipmentAsset.query.filter(
            func.lower(EquipmentAsset.serial_number) == normalized.lower()
        )
        if self.obj_id is not None:
            query = query.filter(EquipmentAsset.id != self.obj_id)
        if query.first() is not None:
            raise ValidationError("This serial number is already in use.")

    def validate_purchase_vendor_id(self, field):
        if field.data and db.session.get(Vendor, field.data) is None:
            raise ValidationError("Select a valid purchase vendor.")

    def validate_service_vendor_id(self, field):
        if field.data and db.session.get(Vendor, field.data) is None:
            raise ValidationError("Select a valid service vendor.")

    def validate_location_id(self, field):
        if field.data and db.session.get(Location, field.data) is None:
            raise ValidationError("Select a valid location.")

    def validate_assigned_user_id(self, field):
        if field.data and db.session.get(User, field.data) is None:
            raise ValidationError("Select a valid user.")

    def validate(self, **kwargs):
        valid = super().validate(**kwargs)
        if not valid:
            return False

        self.name.data = (self.name.data or "").strip() or None
        self.description.data = (self.description.data or "").strip() or None
        self.service_contact_name.data = (
            (self.service_contact_name.data or "").strip() or None
        )
        self.service_contact_email.data = (
            (self.service_contact_email.data or "").strip() or None
        )
        self.service_contact_phone.data = (
            (self.service_contact_phone.data or "").strip() or None
        )
        self.service_contract_name.data = (
            (self.service_contract_name.data or "").strip() or None
        )
        self.service_contract_reference.data = (
            (self.service_contract_reference.data or "").strip() or None
        )
        self.service_contract_notes.data = (
            (self.service_contract_notes.data or "").strip() or None
        )
        self.sublocation.data = (self.sublocation.data or "").strip() or None

        if (
            self.last_service_on.data
            and self.next_service_due_on.data
            and self.next_service_due_on.data < self.last_service_on.data
        ):
            self.next_service_due_on.errors.append(
                "Next service due date must be on or after the last service date."
            )
            return False

        if (
            self.acquired_on.data
            and self.last_service_on.data
            and self.last_service_on.data < self.acquired_on.data
        ):
            self.last_service_on.errors.append(
                "Last service date cannot be before the acquired date."
            )
            return False

        if (
            self.service_contract_expires_on.data
            and self.acquired_on.data
            and self.service_contract_expires_on.data < self.acquired_on.data
        ):
            self.service_contract_expires_on.errors.append(
                "Service contract expiry cannot be before the acquired date."
            )
            return False

        return True


class EquipmentIntakeBatchForm(FlaskForm):
    equipment_model_id = SelectField(
        "Equipment Model",
        coerce=int,
        validators=[DataRequired()],
        validate_choice=False,
    )
    source_type = SelectField(
        "Source",
        validators=[DataRequired()],
        choices=EquipmentIntakeBatch.SOURCE_TYPE_CHOICES,
    )
    expected_quantity = IntegerField(
        "Planned Quantity",
        validators=[DataRequired(), NumberRange(min=1)],
        default=1,
    )
    unit_cost = DecimalField(
        "Expected Unit Cost",
        validators=[Optional(), NumberRange(min=0)],
        places=2,
    )
    purchase_vendor_id = SelectField(
        "Purchased From",
        coerce=int,
        validators=[Optional()],
        validate_choice=False,
        default=0,
    )
    vendor_name = StringField(
        "Recorded Vendor Name",
        validators=[Optional(), Length(max=160)],
    )
    purchase_order_id = SelectField(
        "Linked Purchase Order",
        coerce=int,
        validators=[Optional()],
        validate_choice=False,
        default=0,
    )
    purchase_order_reference = StringField(
        "Purchase Order Reference",
        validators=[Optional(), Length(max=100)],
    )
    purchase_invoice_id = SelectField(
        "Linked Purchase Invoice",
        coerce=int,
        validators=[Optional()],
        validate_choice=False,
        default=0,
    )
    purchase_invoice_reference = StringField(
        "Purchase Invoice Reference",
        validators=[Optional(), Length(max=100)],
    )
    order_date = FlexibleDateField("Order Date", validators=[Optional()])
    expected_received_on = FlexibleDateField(
        "Expected Receive Date", validators=[Optional()]
    )
    received_on = FlexibleDateField("Received On", validators=[Optional()])
    location_id = SelectField(
        "Default Location",
        coerce=int,
        validators=[Optional()],
        validate_choice=False,
        default=0,
    )
    assigned_user_id = SelectField(
        "Default Custodian",
        coerce=int,
        validators=[Optional()],
        validate_choice=False,
        default=0,
    )
    notes = TextAreaField(
        "Notes",
        validators=[Optional(), Length(max=4000)],
    )
    submit = SubmitField("Save Intake Batch")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.equipment_model_id.choices = load_equipment_model_choices(
            include_blank=False
        )
        self.purchase_vendor_id.choices = load_vendor_choices()
        self.purchase_order_id.choices = load_purchase_order_choices()
        self.purchase_invoice_id.choices = load_purchase_invoice_choices()
        self.location_id.choices = load_location_choices()
        self.assigned_user_id.choices = [(0, "No Custodian")] + load_active_user_choices()

    def validate_equipment_model_id(self, field):
        if db.session.get(EquipmentModel, field.data) is None:
            raise ValidationError("Select a valid equipment model.")

    def validate_purchase_vendor_id(self, field):
        if field.data and db.session.get(Vendor, field.data) is None:
            raise ValidationError("Select a valid purchase vendor.")

    def validate_purchase_order_id(self, field):
        if field.data and db.session.get(PurchaseOrder, field.data) is None:
            raise ValidationError("Select a valid purchase order.")

    def validate_purchase_invoice_id(self, field):
        if field.data and db.session.get(PurchaseInvoice, field.data) is None:
            raise ValidationError("Select a valid purchase invoice.")

    def validate_location_id(self, field):
        if field.data and db.session.get(Location, field.data) is None:
            raise ValidationError("Select a valid location.")

    def validate_assigned_user_id(self, field):
        if field.data and db.session.get(User, field.data) is None:
            raise ValidationError("Select a valid user.")

    def validate(self, **kwargs):
        valid = super().validate(**kwargs)
        if not valid:
            return False

        self.vendor_name.data = (self.vendor_name.data or "").strip() or None
        self.purchase_order_reference.data = (
            (self.purchase_order_reference.data or "").strip() or None
        )
        self.purchase_invoice_reference.data = (
            (self.purchase_invoice_reference.data or "").strip() or None
        )
        self.notes.data = (self.notes.data or "").strip() or None

        if (
            self.expected_received_on.data
            and self.order_date.data
            and self.expected_received_on.data < self.order_date.data
        ):
            self.expected_received_on.errors.append(
                "Expected receive date cannot be before the order date."
            )
            return False

        if (
            self.received_on.data
            and self.order_date.data
            and self.received_on.data < self.order_date.data
        ):
            self.received_on.errors.append(
                "Received date cannot be before the order date."
            )
            return False

        purchase_order = (
            db.session.get(PurchaseOrder, self.purchase_order_id.data)
            if self.purchase_order_id.data
            else None
        )
        purchase_invoice = (
            db.session.get(PurchaseInvoice, self.purchase_invoice_id.data)
            if self.purchase_invoice_id.data
            else None
        )

        if (
            purchase_order is not None
            and purchase_invoice is not None
            and purchase_invoice.purchase_order_id != purchase_order.id
        ):
            self.purchase_invoice_id.errors.append(
                "The selected purchase invoice does not belong to the selected purchase order."
            )
            return False

        if self.source_type.data == EquipmentIntakeBatch.SOURCE_PURCHASE_ORDER:
            if not self.purchase_order_id.data and not self.purchase_order_reference.data:
                self.purchase_order_reference.errors.append(
                    "Provide a linked purchase order or a purchase order reference."
                )
                return False

        if self.source_type.data == EquipmentIntakeBatch.SOURCE_PURCHASE_INVOICE:
            if not self.purchase_invoice_id.data and not self.purchase_invoice_reference.data:
                self.purchase_invoice_reference.errors.append(
                    "Provide a linked purchase invoice or a purchase invoice reference."
                )
                return False

        return True


class EquipmentIntakeReceiveForm(FlaskForm):
    quantity = IntegerField(
        "Quantity To Receive",
        validators=[DataRequired(), NumberRange(min=1)],
        default=1,
    )
    asset_tag_prefix = StringField(
        "Generated Asset Tag Prefix",
        validators=[Optional(), Length(max=32)],
    )
    starting_number = IntegerField(
        "Starting Number",
        validators=[Optional(), NumberRange(min=1)],
    )
    number_width = IntegerField(
        "Number Width",
        validators=[Optional(), NumberRange(min=1, max=8)],
        default=3,
    )
    name_prefix = StringField(
        "Generated Name Prefix",
        validators=[Optional(), Length(max=120)],
    )
    asset_rows = TextAreaField(
        "Asset Rows",
        validators=[Optional(), Length(max=12000)],
        description="Optional CSV-style rows: asset tag, serial number, name, sublocation.",
    )
    status = SelectField(
        "Asset Status",
        validators=[DataRequired()],
        choices=EquipmentAsset.STATUS_CHOICES,
        default=EquipmentAsset.STATUS_OPERATIONAL,
    )
    acquired_on = FlexibleDateField("Acquired On", validators=[Optional()])
    warranty_expires_on = FlexibleDateField(
        "Warranty Expires On", validators=[Optional()]
    )
    cost = DecimalField(
        "Per-Asset Cost Override",
        validators=[Optional(), NumberRange(min=0)],
        places=2,
    )
    location_id = SelectField(
        "Location",
        coerce=int,
        validators=[Optional()],
        validate_choice=False,
        default=0,
    )
    sublocation = StringField(
        "Sublocation",
        validators=[Optional(), Length(max=120)],
    )
    assigned_user_id = SelectField(
        "Custodian",
        coerce=int,
        validators=[Optional()],
        validate_choice=False,
        default=0,
    )
    submit = SubmitField("Receive Assets")

    def __init__(self, *args, **kwargs):
        self.batch = kwargs.pop("batch", None)
        super().__init__(*args, **kwargs)
        self.location_id.choices = load_location_choices()
        self.assigned_user_id.choices = [(0, "No Custodian")] + load_active_user_choices()
        self._parsed_asset_rows: list[dict[str, str | None]] = []

    @property
    def parsed_asset_rows(self) -> list[dict[str, str | None]]:
        return list(self._parsed_asset_rows)

    def validate_location_id(self, field):
        if field.data and db.session.get(Location, field.data) is None:
            raise ValidationError("Select a valid location.")

    def validate_assigned_user_id(self, field):
        if field.data and db.session.get(User, field.data) is None:
            raise ValidationError("Select a valid user.")

    def _parse_asset_rows(self) -> list[dict[str, str | None]]:
        parsed_rows: list[dict[str, str | None]] = []
        raw_rows = (self.asset_rows.data or "").splitlines()
        for line_number, raw_row in enumerate(raw_rows, start=1):
            cleaned = raw_row.strip()
            if not cleaned:
                continue
            parts = [part.strip() for part in cleaned.split(",")]
            if not parts or not parts[0]:
                self.asset_rows.errors.append(
                    f"Row {line_number} must start with an asset tag."
                )
                return []
            if len(parts) > 4:
                self.asset_rows.errors.append(
                    f"Row {line_number} supports at most 4 comma-separated values."
                )
                return []
            while len(parts) < 4:
                parts.append("")
            parsed_rows.append(
                {
                    "asset_tag": parts[0] or None,
                    "serial_number": parts[1] or None,
                    "name": parts[2] or None,
                    "sublocation": parts[3] or None,
                }
            )
        return parsed_rows

    def validate(self, **kwargs):
        valid = super().validate(**kwargs)
        if not valid:
            return False

        self.asset_tag_prefix.data = (self.asset_tag_prefix.data or "").strip() or None
        self.name_prefix.data = (self.name_prefix.data or "").strip() or None
        self.sublocation.data = (self.sublocation.data or "").strip() or None
        self._parsed_asset_rows = self._parse_asset_rows()
        if self.asset_rows.errors:
            return False

        if self.batch is not None and self.quantity.data:
            remaining_quantity = self.batch.remaining_quantity
            if remaining_quantity > 0 and self.quantity.data > remaining_quantity:
                self.quantity.errors.append(
                    f"Only {remaining_quantity} asset(s) remain on this intake batch."
                )
                return False

        if self._parsed_asset_rows:
            if len(self._parsed_asset_rows) != self.quantity.data:
                self.asset_rows.errors.append(
                    "The number of asset rows must match the quantity to receive."
                )
                return False
        elif not self.asset_tag_prefix.data or self.starting_number.data is None:
            self.asset_rows.errors.append(
                "Provide asset rows or supply an asset tag prefix and starting number."
            )
            return False

        asset_tags: list[str] = []
        serial_numbers: list[str] = []
        if self._parsed_asset_rows:
            asset_tags = [
                str(row["asset_tag"]).strip()
                for row in self._parsed_asset_rows
                if row.get("asset_tag")
            ]
            serial_numbers = [
                str(row["serial_number"]).strip()
                for row in self._parsed_asset_rows
                if row.get("serial_number")
            ]
        else:
            width = self.number_width.data or 3
            start = self.starting_number.data or 1
            asset_tags = [
                f"{self.asset_tag_prefix.data}{str(start + offset).zfill(width)}"
                for offset in range(int(self.quantity.data or 0))
            ]

        if len(asset_tags) != len(set(tag.casefold() for tag in asset_tags)):
            self.asset_rows.errors.append("Generated asset tags must be unique.")
            return False

        if len(serial_numbers) != len(set(serial.casefold() for serial in serial_numbers)):
            self.asset_rows.errors.append("Serial numbers must be unique within this batch.")
            return False

        existing_tags = {
            asset.asset_tag.casefold()
            for asset in EquipmentAsset.query.filter(
                func.lower(EquipmentAsset.asset_tag).in_(
                    [tag.casefold() for tag in asset_tags]
                )
            ).all()
        }
        if existing_tags:
            self.asset_rows.errors.append(
                "One or more asset tags already exist in the system."
            )
            return False

        if serial_numbers:
            existing_serials = {
                asset.serial_number.casefold()
                for asset in EquipmentAsset.query.filter(
                    EquipmentAsset.serial_number.is_not(None),
                    func.lower(EquipmentAsset.serial_number).in_(
                        [serial.casefold() for serial in serial_numbers]
                    ),
                ).all()
                if asset.serial_number
            }
            if existing_serials:
                self.asset_rows.errors.append(
                    "One or more serial numbers already exist in the system."
                )
                return False

        if (
            self.warranty_expires_on.data
            and self.acquired_on.data
            and self.warranty_expires_on.data < self.acquired_on.data
        ):
            self.warranty_expires_on.errors.append(
                "Warranty expiry cannot be before the acquired date."
            )
            return False

        return True


class EquipmentSnipeItImportForm(FlaskForm):
    file = FileField(
        "Snipe-IT CSV Export",
        validators=[FileRequired(), FileAllowed({"csv"}, "CSV only!")],
    )
    default_category_name = StringField(
        "Default Category",
        validators=[DataRequired(), Length(max=100)],
        default="Imported Equipment",
    )
    create_missing_locations = BooleanField(
        "Create missing locations",
        default=True,
    )
    update_existing = BooleanField(
        "Update existing assets when asset tags already exist",
        default=True,
    )
    submit = SubmitField("Run Import")

    def validate_default_category_name(self, field):
        normalized = (field.data or "").strip()
        if not normalized:
            raise ValidationError("Default category is required.")
        field.data = normalized


class EquipmentMaintenanceIssueForm(FlaskForm):
    equipment_asset_id = SelectField(
        "Equipment Asset",
        coerce=int,
        validators=[DataRequired()],
        validate_choice=False,
    )
    title = StringField(
        "Issue Title",
        validators=[DataRequired(), Length(max=200)],
    )
    description = TextAreaField(
        "Issue Description",
        validators=[Optional(), Length(max=4000)],
    )
    priority = SelectField(
        "Priority",
        validators=[DataRequired()],
        choices=EquipmentMaintenanceIssue.PRIORITY_CHOICES,
    )
    status = SelectField(
        "Status",
        validators=[DataRequired()],
        choices=EquipmentMaintenanceIssue.STATUS_CHOICES,
    )
    reported_on = FlexibleDateField(
        "Reported On",
        validators=[DataRequired()],
        default=date.today,
    )
    due_on = FlexibleDateField("Due On", validators=[Optional()])
    assigned_user_id = SelectField(
        "Assigned Staff",
        coerce=int,
        validators=[Optional()],
        validate_choice=False,
        default=0,
    )
    assigned_vendor_id = SelectField(
        "Assigned Vendor",
        coerce=int,
        validators=[Optional()],
        validate_choice=False,
        default=0,
    )
    parts_cost = DecimalField(
        "Parts Cost",
        validators=[Optional(), NumberRange(min=0)],
        places=2,
    )
    labor_cost = DecimalField(
        "Labor Cost",
        validators=[Optional(), NumberRange(min=0)],
        places=2,
    )
    downtime_started_on = FlexibleDateField(
        "Downtime Started On", validators=[Optional()]
    )
    downtime_resolved_on = FlexibleDateField(
        "Downtime Ended On", validators=[Optional()]
    )
    resolved_on = FlexibleDateField("Resolved On", validators=[Optional()])
    resolution_summary = TextAreaField(
        "Resolution Summary",
        validators=[Optional(), Length(max=4000)],
    )
    submit = SubmitField("Save Issue")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.equipment_asset_id.choices = load_equipment_asset_choices(
            include_blank=False
        )
        self.assigned_user_id.choices = [(0, "Unassigned")] + load_active_user_choices()
        self.assigned_vendor_id.choices = load_vendor_choices()

    def validate_equipment_asset_id(self, field):
        if db.session.get(EquipmentAsset, field.data) is None:
            raise ValidationError("Select a valid equipment asset.")

    def validate_assigned_user_id(self, field):
        if field.data and db.session.get(User, field.data) is None:
            raise ValidationError("Select a valid assigned user.")

    def validate_assigned_vendor_id(self, field):
        if field.data and db.session.get(Vendor, field.data) is None:
            raise ValidationError("Select a valid assigned vendor.")

    def validate(self, **kwargs):
        valid = super().validate(**kwargs)
        if not valid:
            return False

        self.title.data = (self.title.data or "").strip()
        self.description.data = (self.description.data or "").strip() or None
        self.resolution_summary.data = (
            (self.resolution_summary.data or "").strip() or None
        )

        if not self.title.data:
            self.title.errors.append("Issue title is required.")
            return False

        if (
            self.due_on.data
            and self.reported_on.data
            and self.due_on.data < self.reported_on.data
        ):
            self.due_on.errors.append(
                "Due date must be on or after the reported date."
            )
            return False

        if (
            self.downtime_started_on.data
            and self.downtime_resolved_on.data
            and self.downtime_resolved_on.data < self.downtime_started_on.data
        ):
            self.downtime_resolved_on.errors.append(
                "Downtime end date must be on or after the downtime start date."
            )
            return False

        if (
            self.resolved_on.data
            and self.reported_on.data
            and self.resolved_on.data < self.reported_on.data
        ):
            self.resolved_on.errors.append(
                "Resolved date must be on or after the reported date."
            )
            return False

        if (
            self.status.data == EquipmentMaintenanceIssue.STATUS_RESOLVED
            and not self.resolved_on.data
        ):
            self.resolved_on.data = date.today()

        return True


class EquipmentMaintenanceUpdateForm(FlaskForm):
    message = TextAreaField(
        "Update",
        validators=[Optional(), Length(max=4000)],
    )
    status = SelectField(
        "Status",
        validators=[Optional()],
        choices=[("", "Keep current status")]
        + list(EquipmentMaintenanceIssue.STATUS_CHOICES),
    )
    submit = SubmitField("Add Update")

    def validate(self, **kwargs):
        valid = super().validate(**kwargs)
        if not valid:
            return False
        self.message.data = (self.message.data or "").strip() or None
        if not self.message.data and not self.status.data:
            self.message.errors.append(
                "Add a note or choose a status change."
            )
            return False
        return True


class ProductForm(FlaskForm):
    name = StringField("Name", validators=[DataRequired()])
    gl_code = SelectField("GL Code", validators=[Optional()])
    price = DecimalField(
        "Terminal/Event Sell Price",
        validators=[InputRequired(), NumberRange(min=0)],
        places=2,
    )
    invoice_sale_price = DecimalField(
        "Sales Invoice Price (3rd-party customer)",
        validators=[Optional(), NumberRange(min=0)],
        places=2,
    )
    cost = DecimalField(
        "Cost", validators=[Optional(), NumberRange(min=0)], default=0.0
    )
    auto_update_recipe_cost = BooleanField("Auto-update cost")
    gl_code_id = SelectField(
        "GL Code", coerce=int, validators=[Optional()], validate_choice=False
    )
    sales_gl_code = SelectField(
        "Sales GL Code", coerce=int, validators=[Optional()]
    )
    submit = SubmitField("Submit")

    def __init__(self, *args, **kwargs):
        super(ProductForm, self).__init__(*args, **kwargs)
        sales_codes_raw = (
            GLCode.query.filter(GLCode.code.like("4%"))
            .order_by(GLCode.code)
            .all()
        )
        formatted_sales_codes = [
            (
                g.id,
                f"{g.code} - {g.description}" if g.description else g.code,
            )
            for g in sales_codes_raw
        ]
        self.gl_code.choices = [(g.code, g.code) for g in sales_codes_raw]
        self.gl_code_id.choices = formatted_sales_codes
        self.sales_gl_code.choices = formatted_sales_codes

    def validate(self, extra_validators=None):
        valid = super().validate(extra_validators=extra_validators)
        if not self.auto_update_recipe_cost.data and self.cost.data is None:
            self.cost.errors.append(
                "Cost is required unless auto-update cost is enabled."
            )
            return False
        return valid


class BulkProductUpdateForm(FlaskForm):
    """Form used to apply bulk updates to products."""

    selected_ids = HiddenField(validators=[DataRequired()])
    apply_name = BooleanField("Apply")
    name = StringField("Name", validators=[Optional(), Length(max=100)])
    apply_price = BooleanField("Apply")
    price = DecimalField(
        "Price", validators=[Optional(), NumberRange(min=0)], places=None
    )
    apply_cost = BooleanField("Apply")
    cost = DecimalField(
        "Cost", validators=[Optional(), NumberRange(min=0)], places=None
    )
    apply_sales_gl_code_id = BooleanField("Apply")
    sales_gl_code_id = SelectField(
        "Sales GL Code",
        coerce=int,
        validators=[Optional()],
        validate_choice=False,
    )
    apply_gl_code_id = BooleanField("Apply")
    gl_code_id = SelectField(
        "Inventory GL Code",
        coerce=int,
        validators=[Optional()],
        validate_choice=False,
    )
    submit = SubmitField("Apply Updates")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        sales_codes_raw = (
            GLCode.query.filter(GLCode.code.like("4%"))
            .order_by(GLCode.code)
            .all()
        )
        formatted_sales_codes = [
            (
                code.id,
                f"{code.code} - {code.description}" if code.description else code.code,
            )
            for code in sales_codes_raw
        ]
        purchase_codes = ItemForm._fetch_purchase_gl_codes()
        formatted_inventory = [
            (
                code.id,
                f"{code.code} - {code.description}" if code.description else code.code,
            )
            for code in purchase_codes
        ]
        self.sales_gl_code_id.choices = [(0, "Unassigned")] + formatted_sales_codes
        self.gl_code_id.choices = [(0, "Unassigned")] + formatted_inventory

    def validate(self, **kwargs):
        valid = super().validate(**kwargs)
        if not valid:
            return False

        apply_fields = [
            self.apply_name.data,
            self.apply_price.data,
            self.apply_cost.data,
            self.apply_sales_gl_code_id.data,
            self.apply_gl_code_id.data,
        ]
        if not any(apply_fields):
            self.form_errors.append("Select at least one field to update.")
            return False

        if self.apply_name.data and not self.name.data:
            self.name.errors.append("Enter a name to apply.")
            return False

        if self.apply_price.data and self.price.data is None:
            self.price.errors.append("Enter a price value.")
            return False

        if self.apply_cost.data and self.cost.data is None:
            self.cost.errors.append("Enter a cost value.")
            return False

        return True

    def validate_gl_code(self, field):
        if field.data and not str(field.data).startswith("4"):
            raise ValidationError("Product GL codes must start with 4")
        from app.models import GLCode

        sales_codes_raw = (
            GLCode.query.filter(GLCode.code.like("4%"))
            .order_by(GLCode.code)
            .all()
        )
        formatted_sales_codes = [
            (
                g.id,
                f"{g.code} - {g.description}" if g.description else g.code,
            )
            for g in sales_codes_raw
        ]
        self.gl_code_id.choices = formatted_sales_codes
        self.sales_gl_code.choices = formatted_sales_codes


class RecipeItemForm(FlaskForm):
    item = SelectField("Item", coerce=int)
    unit = SelectField(
        "Unit", coerce=int, validators=[Optional()], validate_choice=False
    )
    quantity = DecimalField("Quantity", validators=[InputRequired()])
    countable = BooleanField("Countable")


class ProductRecipeForm(FlaskForm):
    recipe_yield_quantity = DecimalField(
        "Recipe Yield Quantity",
        validators=[Optional(), NumberRange(min=0.0001)],
        default=1,
    )
    recipe_yield_unit = StringField(
        "Recipe Yield Unit", validators=[Optional(), Length(max=50)]
    )
    items = FieldList(FormField(RecipeItemForm), min_entries=1)
    submit = SubmitField("Submit")

    def __init__(self, *args, **kwargs):
        super(ProductRecipeForm, self).__init__(*args, **kwargs)
        items = load_item_choices()
        units = load_unit_choices()
        for item_form in self.items:
            item_form.item.choices = items
            item_form.unit.choices = units


class ProductWithRecipeForm(ProductForm):
    """Form used on product create/edit pages to also manage recipe items."""

    recipe_yield_quantity = DecimalField(
        "Recipe Yield Quantity",
        validators=[Optional(), NumberRange(min=0.0001)],
        default=1,
    )
    recipe_yield_unit = StringField(
        "Recipe Yield Unit", validators=[Optional(), Length(max=50)]
    )
    items = FieldList(FormField(RecipeItemForm), min_entries=0)

    def __init__(self, *args, **kwargs):
        super(ProductWithRecipeForm, self).__init__(*args, **kwargs)
        self.countable_label = RecipeItemForm().countable.label.text
        items = load_item_choices()
        units = load_unit_choices()
        for item_form in self.items:
            item_form.item.choices = items
            item_form.unit.choices = units


class QuickProductForm(FlaskForm):
    """Simplified product form used for quick product creation."""

    name = StringField("Product Name", validators=[DataRequired(), Length(max=100)])
    price = DecimalField(
        "Terminal/Event Sell Price",
        validators=[InputRequired(), NumberRange(min=0)],
        places=2,
    )
    invoice_sale_price = DecimalField(
        "Sales Invoice Price (3rd-party customer)",
        validators=[Optional(), NumberRange(min=0)],
        places=2,
    )
    cost = DecimalField(
        "Cost", validators=[Optional(), NumberRange(min=0)], default=0.0
    )
    sales_gl_code = SelectField(
        "Sales GL Code", coerce=int, validators=[Optional()], validate_choice=False
    )
    recipe_yield_quantity = DecimalField(
        "Recipe Yield Quantity",
        validators=[Optional(), NumberRange(min=0.0001)],
        default=1,
    )
    recipe_yield_unit = StringField(
        "Recipe Yield Unit", validators=[Optional(), Length(max=50)]
    )
    items = FieldList(FormField(RecipeItemForm), min_entries=0)
    submit = SubmitField("Create Product")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        sales_codes_raw = (
            GLCode.query.filter(GLCode.code.like("4%"))
            .order_by(GLCode.code)
            .all()
        )
        self.sales_gl_code.choices = [
            (0, "No Sales GL Code")
        ] + [
            (
                g.id,
                f"{g.code} - {g.description}" if g.description else g.code,
            )
            for g in sales_codes_raw
        ]

        # Populate recipe item choices so quick created products can include recipes.
        self.countable_label = RecipeItemForm().countable.label.text
        items = load_item_choices()
        units = load_unit_choices()
        for item_form in self.items:
            item_form.item.choices = items
            item_form.unit.choices = units


class InvoiceForm(FlaskForm):
    customer = SelectField(
        "Customer", coerce=float, validators=[DataRequired()]
    )
    products = HiddenField("Products JSON")
    submit = SubmitField("Add Product")


class BulkInvoicePaymentForm(FlaskForm):
    """Form used to bulk-update invoice workflow status."""

    selected_ids = HiddenField(
        validators=[DataRequired(message="Select at least one invoice.")]
    )
    payment_status = SelectField(
        "Invoice Status",
        choices=[
            ("delivered", "Delivered"),
            ("paid", "Paid"),
        ],
        validators=[DataRequired(message="Select a valid invoice status.")],
        validate_choice=False,
    )
    submit = SubmitField("Apply")

    def validate(self, **kwargs):
        valid = super().validate(**kwargs)
        if not valid:
            return False

        selected_ids = [
            value.strip()
            for value in str(self.selected_ids.data or "").split(",")
            if value and value.strip()
        ]
        if not selected_ids:
            self.selected_ids.errors.append("Select at least one invoice.")
            return False

        payment_status = str(self.payment_status.data or "").strip().lower()
        if payment_status not in {"delivered", "paid"}:
            self.payment_status.errors.append("Select a valid invoice status.")
            return False

        self.selected_ids.data = ",".join(selected_ids)
        self.payment_status.data = payment_status
        return True


class VendorInvoiceReportForm(FlaskForm):
    payment_status = SelectField(
        "Payment Status",
        choices=[
            ("all", "All"),
            ("paid", "Paid"),
            ("unpaid", "Unpaid"),
        ],
        default="all",
        validators=[Optional()],
    )
    customer = SelectMultipleField(
        "Customer(s)",
        coerce=int,
        validators=[Optional()],
        render_kw={"size": 12},
    )
    start_date = DateField("Start Date", validators=[DataRequired()])
    end_date = DateField("End Date", validators=[DataRequired()])
    submit = SubmitField("Generate Report")


class ReceivedInvoiceReportForm(FlaskForm):
    """Report form for received purchase invoices."""

    start_date = DateField("Start Date", validators=[DataRequired()])
    end_date = DateField("End Date", validators=[DataRequired()])
    submit = SubmitField("Generate Report")


class EquipmentProcurementReportForm(FlaskForm):
    start_date = DateField("Start Date", validators=[Optional()])
    end_date = DateField("End Date", validators=[Optional()])
    category_id = SelectField(
        "Category",
        coerce=int,
        validators=[Optional()],
        validate_choice=False,
        default=0,
    )
    equipment_model_id = SelectField(
        "Equipment Model",
        coerce=int,
        validators=[Optional()],
        validate_choice=False,
        default=0,
    )
    purchase_vendor_id = SelectField(
        "Vendor",
        coerce=int,
        validators=[Optional()],
        validate_choice=False,
        default=0,
    )
    location_id = SelectField(
        "Location",
        coerce=int,
        validators=[Optional()],
        validate_choice=False,
        default=0,
    )
    source_type = SelectField(
        "Source",
        choices=[("all", "All")] + list(EquipmentIntakeBatch.SOURCE_TYPE_CHOICES),
        default="all",
        validators=[Optional()],
    )
    status = SelectField(
        "Status",
        choices=[("all", "All")] + list(EquipmentIntakeBatch.STATUS_CHOICES),
        default="all",
        validators=[Optional()],
    )
    submit = SubmitField("Generate Report")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.category_id.choices = load_equipment_category_choices()
        self.equipment_model_id.choices = load_equipment_model_choices()
        self.purchase_vendor_id.choices = load_vendor_choices()
        self.location_id.choices = load_location_choices()

    def validate(self, **kwargs):
        valid = super().validate(**kwargs)
        if not valid:
            return False
        if (
            self.start_date.data
            and self.end_date.data
            and self.start_date.data > self.end_date.data
        ):
            self.end_date.errors.append(
                "End date must be on or after the start date."
            )
            return False
        return True


# forms.py
# forms.py
class PurchaseInventorySummaryForm(FlaskForm):
    start_date = DateField("Start Date", validators=[DataRequired()])
    end_date = DateField("End Date", validators=[DataRequired()])
    items = SelectMultipleField(
        "Items",
        coerce=int,
        validators=[Optional()],
        render_kw={"size": 10},
    )
    gl_codes = SelectMultipleField(
        "GL Codes",
        coerce=int,
        validators=[Optional()],
        render_kw={"size": 10},
    )
    submit = SubmitField("Generate Report")

    def __init__(self, *args, **kwargs):
        super(PurchaseInventorySummaryForm, self).__init__(*args, **kwargs)
        self.items.choices = load_item_choices()
        self.gl_codes.choices = load_expense_gl_code_choices(
            include_unassigned=True
        )


# forms.py
class InventoryVarianceReportForm(FlaskForm):
    start_date = DateField("Start Date", validators=[DataRequired()])
    end_date = DateField("End Date", validators=[DataRequired()])
    items = SelectMultipleField(
        "Items",
        coerce=int,
        validators=[Optional()],
        render_kw={"size": 10},
    )
    gl_codes = SelectMultipleField(
        "GL Codes",
        coerce=int,
        validators=[Optional()],
        render_kw={"size": 10},
    )
    submit = SubmitField("Generate Report")

    def __init__(self, *args, **kwargs):
        super(InventoryVarianceReportForm, self).__init__(*args, **kwargs)
        self.items.choices = load_item_choices()
        self.gl_codes.choices = load_expense_gl_code_choices(
            include_unassigned=True
        )


# forms.py
class ProductSalesReportForm(FlaskForm):
    start_date = DateField("Start Date", validators=[DataRequired()])
    end_date = DateField("End Date", validators=[DataRequired()])
    payment_status = SelectField(
        "Payment Status",
        choices=[
            ("all", "All"),
            ("paid", "Paid"),
            ("unpaid", "Unpaid"),
        ],
        default="all",
        validators=[Optional()],
    )
    products = SelectMultipleField(
        "Products",
        coerce=int,
        validators=[Optional()],
        render_kw={"size": 10},
    )
    gl_codes = SelectMultipleField(
        "Sales GL Codes",
        coerce=int,
        validators=[Optional()],
        render_kw={"size": 10},
    )
    submit = SubmitField("Generate Report")

    def __init__(self, *args, **kwargs):
        super(ProductSalesReportForm, self).__init__(*args, **kwargs)
        self.products.choices = load_product_choices()
        self.gl_codes.choices = load_sales_gl_code_choices(include_unassigned=True)


class EventTerminalSalesReportForm(FlaskForm):
    start_date = DateField("Start Date", validators=[DataRequired()])
    end_date = DateField("End Date", validators=[DataRequired()])
    submit = SubmitField("Generate Report")

    def validate_end_date(self, field):
        if self.start_date.data and field.data and field.data < self.start_date.data:
            raise ValidationError("End date must be on or after the start date.")


class EventSpoilageReportForm(FlaskForm):
    events = SelectMultipleField(
        "Events",
        coerce=int,
        render_kw={"size": 12},
    )
    submit = SubmitField("Generate Report")

    def __init__(self, *args, **kwargs):
        super(EventSpoilageReportForm, self).__init__(*args, **kwargs)
        self.events.choices = load_event_choices()

    def validate_events(self, field):
        if not field.data:
            raise ValidationError("Select at least one event.")


class ProductRecipeReportForm(FlaskForm):
    products = SelectMultipleField(
        "Products",
        coerce=int,
        validators=[Optional()],
        option_widget=CheckboxInput(),
        widget=ListWidget(prefix_label=False),
    )
    select_all = BooleanField("Select All Products")
    submit = SubmitField("Generate Report")

    def __init__(self, *args, product_choices=None, **kwargs):
        super(ProductRecipeReportForm, self).__init__(*args, **kwargs)
        self.products.choices = product_choices or []


class InvoiceFilterForm(FlaskForm):
    invoice_id = StringField("Invoice ID", validators=[Optional()])
    customer_id = SelectField("Customer", coerce=int, validators=[Optional()])
    start_date = DateField("Start Date", validators=[Optional()])
    end_date = DateField("End Date", validators=[Optional()])
    submit = SubmitField("Filter")


class ActivityLogFilterForm(FlaskForm):
    user_id = SelectField(
        "User",
        coerce=int,
        validators=[Optional()],
        validate_choice=False,
    )
    activity = StringField("Activity Contains", validators=[Optional()])
    start_date = DateField("Start Date", validators=[Optional()])
    end_date = DateField("End Date", validators=[Optional()])
    submit = SubmitField("Filter")


class CreateBackupForm(FlaskForm):
    submit = SubmitField("Create Backup")


class RestoreBackupForm(FlaskForm):
    file = FileField(
        "Backup File",
        validators=[
            FileRequired(),
            FileAllowed({"db"}, "DB files only!"),
        ],
    )
    ignore_favorites = BooleanField(
        "Ignore favorites from backup (clear all user favorites)"
    )
    restore_mode = SelectField(
        "Restore mode",
        choices=[
            ("strict", "Strict (stop on first invalid row)"),
            ("permissive", "Permissive (skip invalid rows and continue)"),
        ],
        default="strict",
    )
    submit = SubmitField("Restore")

    def validate_file(self, field):
        field.data.seek(0, os.SEEK_END)
        if field.data.tell() > MAX_BACKUP_SIZE:
            raise ValidationError("File is too large.")
        field.data.seek(0)


class ImportForm(FlaskForm):
    """Upload a CSV file for bulk imports."""

    file = FileField(
        "CSV File",
        validators=[FileRequired(), FileAllowed({"csv"}, "CSV only!")],
    )
    submit = SubmitField("Import")


class POItemForm(FlaskForm):
    item = HiddenField("Item")
    cost = HiddenField("Cost")
    vendor_sku = StringField(
        "Vendor SKU",
        validators=[Optional(), Length(max=100)],
        render_kw={"autocomplete": "off"},
    )
    vendor_description = HiddenField(
        "Vendor Description", validators=[Optional(), Length(max=255)]
    )
    pack_size = HiddenField(
        "Pack Size", validators=[Optional(), Length(max=100)]
    )
    product = SelectField(
        "Product", coerce=int, validators=[Optional()], validate_choice=False
    )
    unit = SelectField(
        "Unit", coerce=int, validators=[Optional()], validate_choice=False
    )
    quantity = DecimalField("Quantity", validators=[Optional()])
    position = HiddenField("Position")


class PurchaseOrderForm(FlaskForm):
    vendor = SelectField("Vendor", coerce=int, validators=[DataRequired()])
    order_number = StringField("Order Number", validators=[Optional(), Length(max=100)])
    order_date = FlexibleDateField(
        "Order Date",
        validators=[DataRequired()],
        render_kw={"data-flatpickr": "1", "autocomplete": "off"},
    )
    expected_date = FlexibleDateField(
        "Expected Delivery Date",
        validators=[DataRequired()],
        render_kw={"data-flatpickr": "1", "autocomplete": "off"},
    )
    expected_total_cost = DecimalField("Expected Total Cost", validators=[Optional()])
    delivery_charge = DecimalField(
        "Delivery Charge", validators=[Optional()], default=0
    )
    items = FieldList(FormField(POItemForm), min_entries=1)
    submit = SubmitField("Submit")

    def __init__(self, *args, **kwargs):
        super(PurchaseOrderForm, self).__init__(*args, **kwargs)
        self.vendor.choices = [
            (v.id, f"{v.first_name} {v.last_name}")
            for v in Vendor.query.filter_by(archived=False).all()
        ]
        units = load_unit_choices()
        products = [(p.id, p.name) for p in Product.query.all()]
        for item_form in self.items:
            item_form.product.choices = products
            item_form.unit.choices = units


class PurchaseOrderMergeForm(FlaskForm):
    target_po_id = IntegerField("Target Purchase Order ID", validators=[DataRequired()])
    source_po_ids = StringField(
        "Source Purchase Order IDs",
        validators=[DataRequired()],
        description="Comma or space-separated list",
    )
    require_expected_date_match = BooleanField(
        "Require matching expected date", default=True
    )
    submit = SubmitField("Merge")


class VendorItemAliasResolutionRowForm(FlaskForm):
    vendor_sku = HiddenField("Vendor SKU")
    vendor_description = HiddenField("Vendor Description")
    pack_size = HiddenField("Pack/Size")
    quantity = HiddenField("Quantity")
    unit_cost = HiddenField("Unit Cost")
    item_id = SelectField(
        "Item", coerce=int, validators=[DataRequired()], validate_choice=False
    )
    unit_id = SelectField(
        "Default Unit", coerce=int, validators=[Optional()], validate_choice=False
    )


class VendorItemAliasResolutionForm(FlaskForm):
    vendor_id = HiddenField("Vendor", validators=[DataRequired()])
    parsed_payload = HiddenField(validators=[DataRequired()])
    unresolved_payload = HiddenField(validators=[DataRequired()])
    order_date = HiddenField()
    expected_date = HiddenField()
    order_number = HiddenField()
    expected_total_cost = HiddenField()
    rows = FieldList(FormField(VendorItemAliasResolutionRowForm), min_entries=0)
    submit = SubmitField("Save mappings")


class InvoiceItemReceiveForm(FlaskForm):
    item = SelectField("Item", coerce=int)
    vendor_sku = StringField(
        "Vendor SKU",
        validators=[Optional(), Length(max=100)],
        render_kw={"autocomplete": "off"},
    )
    vendor_description = HiddenField(
        "Vendor Description", validators=[Optional(), Length(max=255)]
    )
    pack_size = HiddenField(
        "Pack Size", validators=[Optional(), Length(max=100)]
    )
    unit = SelectField(
        "Unit", coerce=int, validators=[Optional()], validate_choice=False
    )
    quantity = DecimalField("Quantity", validators=[Optional()])
    cost = DecimalField("Cost", validators=[Optional()])
    container_deposit = DecimalField(
        "Container Deposit", validators=[Optional()], default=0
    )
    position = HiddenField("Position")
    location_id = SelectField(
        "Location",
        coerce=int,
        validators=[Optional()],
        validate_choice=False,
    )
    gl_code = SelectField(
        "GL Code",
        coerce=int,
        validators=[Optional()],
        validate_choice=False,
    )


class ReceiveInvoiceForm(FlaskForm):
    invoice_number = StringField("Invoice Number", validators=[Optional()])
    received_date = FlexibleDateField(
        "Received Date",
        validators=[DataRequired()],
        default=date.today,
        render_kw={"data-flatpickr": "1", "autocomplete": "off"},
    )
    location_id = SelectField(
        "Location", coerce=int, validators=[DataRequired()] 
    )
    department = SelectField(
        "Department",
        choices=[("", "—")] + PURCHASE_RECEIVE_DEPARTMENT_CHOICES,
        validators=[Optional()],
    )
    gst = DecimalField("GST Amount", validators=[Optional()], default=0)
    pst = DecimalField("PST Amount", validators=[Optional()], default=0)
    delivery_charge = DecimalField(
        "Delivery Charge", validators=[Optional()], default=0
    )
    items = FieldList(FormField(InvoiceItemReceiveForm), min_entries=1)
    submit = SubmitField("Submit")

    def __init__(self, *args, **kwargs):
        super(ReceiveInvoiceForm, self).__init__(*args, **kwargs)
        self.location_id.choices = [
            (loc.id, loc.name)
            for loc in Location.query.filter_by(archived=False).all()
        ]
        items = load_item_choices()
        units = load_unit_choices()
        gl_codes = load_purchase_gl_code_choices()
        location_choices = [(0, "Use Invoice Location")] + [
            (loc_id, label) for loc_id, label in self.location_id.choices
        ]
        for item_form in self.items:
            item_form.item.choices = items
            item_form.unit.choices = units
            item_form.location_id.choices = location_choices
            if item_form.location_id.data is None:
                item_form.location_id.data = 0
            item_form.gl_code.choices = [
                (value, label) for value, label in gl_codes
            ]


class VendorItemAliasForm(FlaskForm):
    vendor_id = SelectField("Vendor", coerce=int, validators=[DataRequired()])
    vendor_sku = StringField("Vendor SKU", validators=[Optional()])
    vendor_description = StringField("Vendor Description", validators=[Optional()])
    pack_size = StringField("Pack/Size", validators=[Optional()])
    item_id = SelectField("Item", coerce=int, validators=[DataRequired()])
    item_unit_id = SelectField(
        "Default Unit", coerce=int, validators=[Optional()], validate_choice=False
    )
    default_cost = DecimalField("Default Cost", validators=[Optional()])
    return_to = HiddenField()
    submit = SubmitField("Save Alias")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.vendor_id.choices = [
            (v.id, f"{v.first_name} {v.last_name}")
            for v in Vendor.query.filter_by(archived=False).order_by(Vendor.first_name)
        ]
        self.item_id.choices = load_item_choices()
        unit_choices = load_unit_choices()
        self.item_unit_id.choices = [(0, "—")] + unit_choices


class DeleteForm(FlaskForm):
    """Simple form used for CSRF protection on delete actions."""

    submit = SubmitField("Delete")


class TerminalSalesMappingDeleteForm(FlaskForm):
    """Form used to delete stored terminal sales aliases."""

    selected_ids = SelectMultipleField(coerce=int)
    delete_selected = SubmitField("Delete Selected")
    delete_all = SubmitField("Delete All")


class BulkProductCostForm(FlaskForm):
    """Form used when bulk-updating product costs from their recipes."""

    submit = SubmitField("Apply")


class GLCodeForm(FlaskForm):
    code = StringField("Code", validators=[DataRequired(), Length(max=6)])
    description = StringField("Description", validators=[Optional()])
    submit = SubmitField("Submit")


EVENT_TYPES = [
    ("catering", "Catering"),
    ("hockey", "Hockey"),
    ("concert", "Concert"),
    ("RMWF", "RMWF"),
    ("tournament", "Tournament"),
    ("curling", "Curling"),
    ("inventory", "Inventory"),
    ("other", "Other"),
]


class EventForm(FlaskForm):
    name = StringField("Name", validators=[DataRequired()])
    start_date = FlexibleDateField("Start Date", validators=[DataRequired()])
    end_date = FlexibleDateField("End Date", validators=[DataRequired()])
    event_type = SelectField(
        "Event Type", choices=EVENT_TYPES, validators=[DataRequired()]
    )
    estimated_sales = DecimalField(
        "Estimated Sales", validators=[Optional(), NumberRange(min=0)], places=2
    )
    submit = SubmitField("Submit")


class EventLocationForm(FlaskForm):
    location_id = SelectMultipleField(
        "Locations", coerce=int, validators=[DataRequired()]
    )
    submit = SubmitField("Submit")

    def __init__(self, event_id=None, *args, **kwargs):
        super(EventLocationForm, self).__init__(*args, **kwargs)
        existing_location_ids = set()
        if event_id is not None:
            existing_location_ids = {
                loc_id
                for (loc_id,) in EventLocation.query.with_entities(
                    EventLocation.location_id
                ).filter_by(event_id=event_id)
            }

        self.location_id.choices = [
            (loc.id, loc.name)
            for loc in Location.query.filter_by(archived=False)
            .order_by(Location.name)
            .all()
            if loc.id not in existing_location_ids
        ]


class EventLocationConfirmForm(FlaskForm):
    submit = SubmitField("Confirm")


class EventLocationUndoConfirmForm(FlaskForm):
    submit = SubmitField("Undo Confirmation")


class UpdateOpeningCountsForm(FlaskForm):
    location_ids = SelectMultipleField("Locations", coerce=int)
    submit = SubmitField("Update Opening Counts")


class ScanCountForm(FlaskForm):
    upc = StringField("Barcode", validators=[DataRequired(), Length(max=32)])
    quantity = DecimalField(
        "Quantity", validators=[InputRequired()], default=1
    )
    submit = SubmitField("Add Count")


class ConfirmForm(FlaskForm):
    """Generic confirmation form used for warnings."""

    submit = SubmitField("Confirm")


class TerminalSaleForm(FlaskForm):
    product_id = SelectField(
        "Product", coerce=int, validators=[DataRequired()]
    )
    quantity = DecimalField("Quantity", validators=[InputRequired()])
    submit = SubmitField("Submit")

    def __init__(self, *args, **kwargs):
        super(TerminalSaleForm, self).__init__(*args, **kwargs)
        self.product_id.choices = [(p.id, p.name) for p in Product.query.all()]


class TerminalSalesUploadForm(FlaskForm):
    """Form for uploading terminal sales from legacy Excel exports."""

    program = SelectField(
        "Point of Sale Program",
        choices=[("idealpos", "IdealPOS")],
        default="idealpos",
    )
    file = FileField(
        "Sales File",
        validators=[
            FileRequired(),
            FileAllowed(
                {"xls", "xlsx", "pdf"},
                "Only .xls, .xlsx, or .pdf files are allowed.",
            ),
        ],
    )
    submit = SubmitField("Upload")


class DepartmentSalesForecastForm(FlaskForm):
    """Upload IdealPOS exports for department sales forecasting."""

    upload = FileField(
        "Department Sales Export",
        validators=[
            FileRequired(),
            FileAllowed({"xls", "xlsx"}, "Only .xls or .xlsx files are allowed."),
        ],
    )
    only_mapped_products = BooleanField(
        "Only include mapped products in usage totals",
        default=False,
    )
    submit = SubmitField("Upload")


class SettingsForm(FlaskForm):
    gst_number = StringField(
        "GST Number", validators=[Optional(), Length(max=50)]
    )
    retail_pop_price = DecimalField(
        "Retail Pop Price",
        validators=[Optional(), NumberRange(min=0)],
        places=2,
    )
    default_timezone = SelectField("Default Timezone")
    auto_backup_enabled = BooleanField("Enable Automatic Backups")
    auto_backup_interval_value = IntegerField(
        "Backup Interval",
        validators=[DataRequired(), NumberRange(min=1)],
    )
    auto_backup_interval_unit = SelectField(
        "Interval Unit",
        choices=[
            ("hour", "Hour"),
            ("day", "Day"),
            ("week", "Week"),
            ("month", "Month"),
            ("year", "Year"),
        ],
    )
    pos_sales_import_interval_value = IntegerField(
        "POS Sales Import Lookback",
        validators=[DataRequired(), NumberRange(min=1)],
    )
    pos_sales_import_interval_unit = SelectField(
        "POS Sales Import Unit",
        choices=[
            ("hour", "Hour"),
            ("day", "Day"),
            ("week", "Week"),
        ],
    )
    max_backups = IntegerField(
        "Max Stored Backups",
        validators=[DataRequired(), NumberRange(min=1)],
    )
    convert_ounce = SelectField("Ounce")
    convert_gram = SelectField("Gram")
    convert_each = SelectField("Each")
    convert_millilitre = SelectField("Millilitre")
    receive_default_kitchen = SelectField(
        "Kitchen Default Location", coerce=int, validate_choice=False
    )
    receive_default_concessions = SelectField(
        "Concessions Default Location", coerce=int, validate_choice=False
    )
    receive_default_banquets = SelectField(
        "Banquets Default Location", coerce=int, validate_choice=False
    )
    receive_default_beverages = SelectField(
        "Beverages Default Location", coerce=int, validate_choice=False
    )
    receive_default_office = SelectField(
        "Office Default Location", coerce=int, validate_choice=False
    )
    receive_default_other = SelectField(
        "Other Default Location", coerce=int, validate_choice=False
    )
    enable_sysco_imports = BooleanField("SYSCO", default=True)
    enable_pratts_imports = BooleanField("PRATTS", default=True)
    enable_manitoba_liquor_imports = BooleanField(
        "MANITOBA LIQUOR & LOTTERIES", default=True
    )
    submit = SubmitField("Update")

    def __init__(
        self,
        *args,
        base_unit_mapping=None,
        receive_location_defaults=None,
        purchase_import_vendors=None,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        self.default_timezone.choices = [
            (tz, tz) for tz in get_timezone_choices()
        ]
        mapping = base_unit_mapping or {}
        for unit in BASE_UNITS:
            field = getattr(self, f"convert_{unit}")
            field.choices = [
                (target, get_unit_label(target))
                for target in get_allowed_target_units(unit)
            ]
            if not self.is_submitted():
                field.data = mapping.get(unit, unit)

        location_choices = [
            (0, "No default")
        ] + [
            (loc.id, loc.name)
            for loc in Location.query.filter_by(archived=False)
            .order_by(Location.name)
            .all()
        ]
        defaults = receive_location_defaults or {}
        for department, _, field_name in PURCHASE_RECEIVE_DEPARTMENT_CONFIG:
            field = getattr(self, field_name)
            field.choices = location_choices
            if not self.is_submitted():
                field.data = defaults.get(department, 0)

        enabled_vendors = {
            vendor.upper() for vendor in (purchase_import_vendors or [])
        }
        for vendor_label, field in self.iter_purchase_import_vendors():
            if not self.is_submitted():
                field.data = vendor_label.upper() in enabled_vendors

    def iter_base_unit_conversions(self):
        for unit in BASE_UNITS:
            field = getattr(self, f"convert_{unit}")
            yield unit, get_unit_label(unit), field

    def iter_receive_location_defaults(self):
        for _, label, field_name in PURCHASE_RECEIVE_DEPARTMENT_CONFIG:
            yield label, getattr(self, field_name)

    def iter_purchase_import_vendors(self):
        return [
            ("SYSCO", self.enable_sysco_imports),
            ("PRATTS", self.enable_pratts_imports),
            (
                "MANITOBA LIQUOR & LOTTERIES",
                self.enable_manitoba_liquor_imports,
            ),
        ]


class TimezoneForm(FlaskForm):
    timezone = SelectField("Timezone", validators=[Optional()])
    submit = SubmitField("Update Timezone")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.timezone.choices = [("", "Use Default")] + [
            (tz, tz) for tz in get_timezone_choices()
        ]


class NotificationForm(FlaskForm):
    phone_number = StringField(
        "Phone Number", validators=[Optional(), Length(max=20)]
    )
    notify_transfers = BooleanField("Send text on new transfer")
    notify_schedule_post_email = BooleanField("Email when my schedule is posted")
    notify_schedule_post_text = BooleanField("Text when my schedule is posted")
    notify_schedule_changes_email = BooleanField(
        "Email when my published shifts change"
    )
    notify_schedule_changes_text = BooleanField(
        "Text when my published shifts change"
    )
    notify_tradeboard_email = BooleanField(
        "Email when tradeboard/open shifts match my positions"
    )
    notify_tradeboard_text = BooleanField(
        "Text when tradeboard/open shifts match my positions"
    )
    submit = SubmitField("Update Notifications")


class DepartmentForm(FlaskForm):
    name = StringField("Department Name", validators=[DataRequired(), Length(max=100)])
    description = TextAreaField("Description", validators=[Optional(), Length(max=1000)])
    active = BooleanField("Active", default=True)
    submit = SubmitField("Save Department")


class ShiftPositionForm(FlaskForm):
    department_id = SelectField(
        "Department",
        coerce=int,
        validators=[DataRequired()],
        validate_choice=False,
    )
    name = StringField("Position Name", validators=[DataRequired(), Length(max=100)])
    description = TextAreaField("Description", validators=[Optional(), Length(max=1000)])
    default_color = SelectField("Default Color", validators=[Optional()])
    sort_order = IntegerField("Sort Order", validators=[Optional()])
    active = BooleanField("Active", default=True)
    submit = SubmitField("Save Position")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.department_id.choices = load_schedule_department_choices()
        self.default_color.choices = [
            ("", "Use Shift Color"),
            ("text-primary", "Blue"),
            ("text-success", "Green"),
            ("text-danger", "Red"),
            ("text-warning", "Orange"),
            ("text-info", "Cyan"),
            ("text-dark", "Black"),
        ]


class ScheduleTemplateCreateForm(FlaskForm):
    name = StringField("Template Name", validators=[DataRequired(), Length(max=120)])
    description = TextAreaField(
        "Description", validators=[Optional(), Length(max=2000)]
    )
    department_id = SelectField(
        "Department",
        coerce=int,
        validators=[DataRequired()],
        validate_choice=False,
    )
    position_id = SelectField(
        "Position",
        coerce=int,
        validators=[DataRequired()],
        validate_choice=False,
    )
    span = SelectField(
        "Template Period",
        choices=list(ScheduleTemplate.SPAN_CHOICES),
        validators=[DataRequired()],
    )
    active = BooleanField("Active", default=True)
    submit = SubmitField("Create Template")

    def __init__(
        self,
        *args,
        department_choices: list[tuple[int, str]] | None = None,
        position_choices: list[tuple[int, str]] | None = None,
        department_id: int | None = None,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        self.department_id.choices = (
            department_choices or load_schedule_department_choices()
        )
        if self.department_id.data in (None, "") and department_id:
            self.department_id.data = department_id
        self.position_id.choices = (
            position_choices
            if position_choices is not None
            else load_schedule_position_choices(department_id=department_id)
        )


class ScheduleTemplateUpdateForm(FlaskForm):
    name = StringField("Template Name", validators=[DataRequired(), Length(max=120)])
    description = TextAreaField(
        "Description", validators=[Optional(), Length(max=2000)]
    )
    active = BooleanField("Active", default=True)
    submit = SubmitField("Save Template")


class ScheduleTemplateEntryForm(FlaskForm):
    entry_id = HiddenField(validators=[Optional()])
    weekday = SelectField(
        "Weekday",
        coerce=int,
        validators=[Optional()],
        validate_choice=False,
    )
    day_of_month = IntegerField(
        "Day of Month",
        validators=[Optional(), NumberRange(min=1, max=31)],
    )
    month_of_year = SelectField(
        "Month",
        coerce=int,
        validators=[Optional()],
        validate_choice=False,
    )
    assignment_mode = SelectField(
        "Shift Type",
        choices=[
            ("assigned", "Assigned"),
            ("open", "Open"),
            ("tradeboard", "Tradeboard"),
        ],
        validators=[DataRequired()],
    )
    assigned_user_id = SelectField(
        "Assigned User",
        coerce=int,
        validators=[Optional()],
        validate_choice=False,
    )
    start_time = TimeField("Start Time", validators=[DataRequired()], format="%H:%M")
    end_time = TimeField("End Time", validators=[DataRequired()], format="%H:%M")
    paid_hours = WTFormsDecimalField(
        "Paid Hours",
        places=2,
        validators=[Optional(), NumberRange(min=0)],
    )
    paid_hours_manual = BooleanField("Use manual paid hours")
    notes = TextAreaField("Notes", validators=[Optional(), Length(max=2000)])
    color = SelectField("Color", validators=[Optional()])
    is_locked = BooleanField("Lock from auto-assign")
    submit = SubmitField("Save Template Shift")

    def __init__(
        self,
        *args,
        template_span: str = ScheduleTemplate.SPAN_WEEK,
        assigned_user_choices: list[tuple[int, str]] | None = None,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        self.template_span = template_span
        self.weekday.choices = [
            (0, "Monday"),
            (1, "Tuesday"),
            (2, "Wednesday"),
            (3, "Thursday"),
            (4, "Friday"),
            (5, "Saturday"),
            (6, "Sunday"),
        ]
        self.month_of_year.choices = [
            (1, "January"),
            (2, "February"),
            (3, "March"),
            (4, "April"),
            (5, "May"),
            (6, "June"),
            (7, "July"),
            (8, "August"),
            (9, "September"),
            (10, "October"),
            (11, "November"),
            (12, "December"),
        ]
        self.assigned_user_id.choices = (
            assigned_user_choices
            if assigned_user_choices is not None
            else [(0, "Unassigned")] + load_active_user_choices()
        )
        self.color.choices = [
            ("", "Use default"),
            ("text-primary", "Blue"),
            ("text-success", "Green"),
            ("text-danger", "Red"),
            ("text-warning", "Orange"),
            ("text-info", "Cyan"),
            ("text-dark", "Black"),
        ]
        if (
            not self.is_submitted()
            and self.weekday.data in (None, "")
            and template_span == ScheduleTemplate.SPAN_WEEK
        ):
            self.weekday.data = 0
        if (
            not self.is_submitted()
            and self.month_of_year.data in (None, "")
            and template_span == ScheduleTemplate.SPAN_YEAR
        ):
            self.month_of_year.data = 1
        if not self.is_submitted() and self.day_of_month.data in (None, ""):
            self.day_of_month.data = 1

    def validate_end_time(self, field):
        if self.start_time.data and field.data and field.data <= self.start_time.data:
            raise ValidationError("End time must be after start time.")

    def validate(self, extra_validators=None):
        valid = super().validate(extra_validators=extra_validators)
        span = self.template_span

        if span == ScheduleTemplate.SPAN_WEEK and self.weekday.data is None:
            self.weekday.errors.append("Select a weekday.")
            valid = False
        elif span == ScheduleTemplate.SPAN_MONTH and not self.day_of_month.data:
            self.day_of_month.errors.append("Enter a day of the month.")
            valid = False
        elif span == ScheduleTemplate.SPAN_YEAR:
            if self.month_of_year.data is None:
                self.month_of_year.errors.append("Select a month.")
                valid = False
            if not self.day_of_month.data:
                self.day_of_month.errors.append("Enter a day of the month.")
                valid = False
            if self.month_of_year.data and self.day_of_month.data:
                try:
                    date(2024, self.month_of_year.data, self.day_of_month.data)
                except ValueError:
                    self.day_of_month.errors.append(
                        "Selected month and day do not form a valid calendar date."
                    )
                    valid = False

        if self.assignment_mode.data == "assigned" and not (
            self.assigned_user_id.data and self.assigned_user_id.data > 0
        ):
            self.assigned_user_id.errors.append(
                "Assigned template shifts require a user."
            )
            valid = False
        return valid


class ScheduleTemplateApplyForm(FlaskForm):
    target_start_date = DateField("Apply To", validators=[DataRequired()])
    submit = SubmitField("Apply Selected Templates")


class UserScheduleProfileForm(FlaskForm):
    hourly_rate = WTFormsDecimalField(
        "Hourly Rate",
        places=2,
        validators=[Optional(), NumberRange(min=0)],
    )
    desired_weekly_hours = WTFormsDecimalField(
        "Preferred Weekly Hours",
        places=2,
        validators=[Optional(), NumberRange(min=0)],
    )
    max_weekly_hours = WTFormsDecimalField(
        "Max Weekly Hours",
        places=2,
        validators=[Optional(), NumberRange(min=0)],
    )
    schedule_enabled = BooleanField("Enable scheduling for this user", default=True)
    schedule_notes = TextAreaField(
        "Scheduling Notes", validators=[Optional(), Length(max=2000)]
    )
    submit = SubmitField("Save Scheduling Settings")


class UserDepartmentMembershipForm(FlaskForm):
    department_id = SelectField(
        "Department",
        coerce=int,
        validators=[DataRequired()],
        validate_choice=False,
    )
    role = StringField(
        "Role",
        validators=[DataRequired(), Length(max=50)],
    )
    reports_to_user_id = SelectField(
        "Reports To",
        coerce=int,
        validators=[Optional()],
        validate_choice=False,
    )
    can_auto_assign = BooleanField("Allow auto-assign for this department")
    is_primary = BooleanField("Primary Department")
    submit = SubmitField("Add Department Membership")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.department_id.choices = load_schedule_department_choices()
        self.reports_to_user_id.choices = [(0, "No direct supervisor")] + load_active_user_choices()
        self.role_suggestions = load_schedule_membership_role_suggestions()
        if not self.role.raw_data and not self.role.data:
            self.role.data = UserDepartmentMembership.ROLE_STAFF

    def validate_role(self, field):
        field.data = UserDepartmentMembership.normalize_role(field.data)


class UserPositionEligibilityForm(FlaskForm):
    position_id = SelectField(
        "Position",
        coerce=int,
        validators=[DataRequired()],
        validate_choice=False,
    )
    priority = IntegerField("Priority", validators=[Optional()])
    active = BooleanField("Active", default=True)
    submit = SubmitField("Add Position")

    def __init__(self, *args, department_id: int | None = None, **kwargs):
        super().__init__(*args, **kwargs)
        self.position_id.choices = load_schedule_position_choices(department_id=department_id)


class ShiftForm(FlaskForm):
    shift_id = HiddenField(validators=[Optional()])
    schedule_week_id = HiddenField(validators=[Optional()])
    shift_date = DateField("Date", validators=[DataRequired()])
    department_id = SelectField(
        "Department",
        coerce=int,
        validators=[DataRequired()],
        validate_choice=False,
    )
    assigned_user_id = SelectField(
        "Assigned User",
        coerce=int,
        validators=[Optional()],
        validate_choice=False,
    )
    position_id = SelectField(
        "Position",
        coerce=int,
        validators=[DataRequired()],
        validate_choice=False,
    )
    assignment_mode = SelectField(
        "Shift Type",
        choices=[
            ("assigned", "Assigned"),
            ("open", "Open"),
            ("tradeboard", "Tradeboard"),
        ],
        validators=[DataRequired()],
    )
    start_time = TimeField("Start Time", validators=[DataRequired()], format="%H:%M")
    end_time = TimeField("End Time", validators=[DataRequired()], format="%H:%M")
    paid_hours = WTFormsDecimalField(
        "Paid Hours",
        places=2,
        validators=[Optional(), NumberRange(min=0)],
    )
    paid_hours_manual = BooleanField("Use manual paid hours")
    location_id = SelectField(
        "Location",
        coerce=int,
        validators=[Optional()],
        validate_choice=False,
    )
    event_id = SelectField(
        "Event",
        coerce=int,
        validators=[Optional()],
        validate_choice=False,
    )
    notes = TextAreaField("Notes", validators=[Optional(), Length(max=2000)])
    color = SelectField("Color", validators=[Optional()])
    is_locked = BooleanField("Lock from auto-assign")
    target_days = SelectMultipleField(
        "Add to days",
        coerce=int,
        validators=[Optional()],
        validate_choice=False,
    )
    copy_count = IntegerField(
        "Create copies",
        validators=[Optional(), NumberRange(min=1, max=20)],
        default=1,
    )
    repeat_weeks = IntegerField(
        "Repeat for more weeks",
        validators=[Optional(), NumberRange(min=0, max=12)],
        default=0,
    )
    submit = SubmitField("Save Shift")

    def __init__(
        self,
        *args,
        department_id: int | None = None,
        assigned_user_choices: list[tuple[int, str]] | None = None,
        department_choices: list[tuple[int, str]] | None = None,
        position_choices: list[tuple[int, str]] | None = None,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        self.department_id.choices = department_choices or load_schedule_department_choices()
        if self.department_id.data in (None, "") and department_id:
            self.department_id.data = department_id
        self.assigned_user_id.choices = (
            assigned_user_choices
            if assigned_user_choices is not None
            else [(0, "Unassigned")] + load_active_user_choices()
        )
        self.position_id.choices = (
            position_choices
            if position_choices is not None
            else load_schedule_position_choices(department_id=department_id)
        )
        self.location_id.choices = [(0, "No location")] + [
            (location.id, location.name)
            for location in Location.query.filter_by(archived=False).order_by(Location.name).all()
        ]
        self.event_id.choices = [(0, "No event")] + [
            (event.id, event.name)
            for event in Event.query.order_by(Event.start_date.desc(), Event.name.asc()).all()
        ]
        self.color.choices = [
            ("", "Use default"),
            ("text-primary", "Blue"),
            ("text-success", "Green"),
            ("text-danger", "Red"),
            ("text-warning", "Orange"),
            ("text-info", "Cyan"),
            ("text-dark", "Black"),
        ]
        self.target_days.choices = [
            (0, "Mon"),
            (1, "Tue"),
            (2, "Wed"),
            (3, "Thu"),
            (4, "Fri"),
            (5, "Sat"),
            (6, "Sun"),
        ]

    def validate_end_time(self, field):
        if self.start_time.data and field.data and field.data <= self.start_time.data:
            raise ValidationError("End time must be after start time.")


class AvailabilityWindowForm(FlaskForm):
    weekday = SelectField(
        "Weekday",
        coerce=int,
        choices=[
            (0, "Monday"),
            (1, "Tuesday"),
            (2, "Wednesday"),
            (3, "Thursday"),
            (4, "Friday"),
            (5, "Saturday"),
            (6, "Sunday"),
        ],
    )
    start_time = TimeField("Start Time", validators=[DataRequired()], format="%H:%M")
    end_time = TimeField("End Time", validators=[DataRequired()], format="%H:%M")
    note = StringField("Note", validators=[Optional(), Length(max=255)])
    submit = SubmitField("Add Availability Window")

    def validate_end_time(self, field):
        if self.start_time.data and field.data and field.data <= self.start_time.data:
            raise ValidationError("End time must be after start time.")


class AvailabilityOverrideForm(FlaskForm):
    start_at = DateTimeLocalField("Start", validators=[DataRequired()], format="%Y-%m-%dT%H:%M")
    end_at = DateTimeLocalField("End", validators=[DataRequired()], format="%Y-%m-%dT%H:%M")
    is_available = BooleanField("Mark this time as available")
    note = StringField("Note", validators=[Optional(), Length(max=255)])
    submit = SubmitField("Add Date Override")

    def validate_end_at(self, field):
        if self.start_at.data and field.data and field.data <= self.start_at.data:
            raise ValidationError("End must be after start.")


class TimeOffRequestForm(FlaskForm):
    start_date = DateField("Start Date", validators=[DataRequired()])
    end_date = DateField("End Date", validators=[DataRequired()])
    start_time = TimeField("Start Time", validators=[Optional()], format="%H:%M")
    end_time = TimeField("End Time", validators=[Optional()], format="%H:%M")
    reason = TextAreaField("Reason", validators=[DataRequired(), Length(max=2000)])
    submit = SubmitField("Submit Request")

    def validate_end_date(self, field):
        if self.start_date.data and field.data and field.data < self.start_date.data:
            raise ValidationError("End date must be on or after the start date.")

    def validate_end_time(self, field):
        if (
            self.start_date.data
            and self.end_date.data
            and self.start_date.data == self.end_date.data
            and self.start_time.data
            and field.data
            and field.data <= self.start_time.data
        ):
            raise ValidationError("End time must be after the start time.")


class TimeOffReviewForm(FlaskForm):
    status = SelectField(
        "Decision",
        choices=[
            ("approved", "Approve"),
            ("denied", "Deny"),
        ],
        validators=[DataRequired()],
    )
    manager_note = TextAreaField(
        "Manager Note", validators=[Optional(), Length(max=2000)]
    )
    submit = SubmitField("Submit Decision")


class TradeboardClaimReviewForm(FlaskForm):
    status = SelectField(
        "Decision",
        choices=[
            ("approved", "Approve"),
            ("rejected", "Reject"),
        ],
        validators=[DataRequired()],
    )
    manager_note = TextAreaField(
        "Manager Note", validators=[Optional(), Length(max=2000)]
    )
    submit = SubmitField("Submit Decision")


class NoteForm(FlaskForm):
    content = TextAreaField(
        "Note", validators=[DataRequired(), Length(max=2000)]
    )
    pinned = BooleanField("Pin note")
    submit = SubmitField("Save Note")


class CommunicationMessageForm(FlaskForm):
    audience = SelectField(
        "Audience",
        choices=[
            ("users", "Selected users"),
            ("department", "Department"),
            ("all", "All scoped users"),
        ],
        validators=[DataRequired()],
    )
    recipient_user_ids = SelectMultipleField(
        "Users",
        coerce=int,
        validators=[Optional()],
        validate_choice=False,
        render_kw={"size": 8},
    )
    department_id = SelectField(
        "Department",
        coerce=int,
        validators=[Optional()],
        validate_choice=False,
    )
    subject = StringField(
        "Subject",
        validators=[DataRequired(), Length(max=200)],
    )
    body = TextAreaField(
        "Message",
        validators=[DataRequired(), Length(max=4000)],
    )
    submit = SubmitField("Send Message")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.department_id.choices = [(0, "Select a department")]

    def validate(self, extra_validators=None):
        if not super().validate(extra_validators=extra_validators):
            return False
        if self.audience.data == "users" and not (self.recipient_user_ids.data or []):
            self.recipient_user_ids.errors.append("Select at least one user.")
            return False
        if self.audience.data == "department" and not self.department_id.data:
            self.department_id.errors.append("Select a department.")
            return False
        return True


class BulletinPostForm(CommunicationMessageForm):
    subject = StringField(
        "Headline",
        validators=[DataRequired(), Length(max=200)],
    )
    body = TextAreaField(
        "Bulletin",
        validators=[DataRequired(), Length(max=4000)],
    )
    submit = SubmitField("Post Bulletin")
