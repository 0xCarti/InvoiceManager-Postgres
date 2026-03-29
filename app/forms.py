import os
from datetime import date
from decimal import Decimal, InvalidOperation
from functools import lru_cache
from datetime import datetime
from zoneinfo import available_timezones

from flask import g
from flask_wtf import FlaskForm
from flask_wtf.file import FileAllowed, FileRequired
from sqlalchemy import or_
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

from app.models import (
    Event,
    EventLocation,
    GLCode,
    Item,
    ItemUnit,
    Location,
    Menu,
    Product,
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


def load_menu_choices(include_blank: bool = True):
    """Return menu options for selection fields."""

    menus = Menu.query.order_by(Menu.name).all()
    choices = [(menu.id, menu.name) for menu in menus]
    if include_blank:
        return [(0, "No Menu")] + choices
    return choices


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
    is_spoilage = BooleanField("Spoilage Location")
    submit = SubmitField("Submit")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.menu_id.choices = load_menu_choices()
        if self.menu_id.data is None:
            self.menu_id.data = 0


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


class ItemForm(FlaskForm):
    name = StringField("Name", validators=[DataRequired()])
    upc = StringField("UPC", validators=[Optional(), Length(max=32)])
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
    submit = SubmitField("Send Invite")


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
        "Cost", validators=[InputRequired(), NumberRange(min=0)], default=0.0
    )
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
    """Form used to bulk-update invoice payment status."""

    selected_ids = HiddenField(
        validators=[DataRequired(message="Select at least one invoice.")]
    )
    payment_status = SelectField(
        "Payment Status",
        choices=[("paid", "Paid"), ("unpaid", "Unpaid")],
        validators=[DataRequired(message="Select a valid payment status.")],
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
        if payment_status not in {"paid", "unpaid"}:
            self.payment_status.errors.append("Select a valid payment status.")
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
        option_widget=CheckboxInput(),
        widget=ListWidget(prefix_label=False),
    )
    start_date = DateField("Start Date", validators=[DataRequired()])
    end_date = DateField("End Date", validators=[DataRequired()])
    submit = SubmitField("Generate Report")


class ReceivedInvoiceReportForm(FlaskForm):
    """Report form for received purchase invoices."""

    start_date = DateField("Start Date", validators=[DataRequired()])
    end_date = DateField("End Date", validators=[DataRequired()])
    submit = SubmitField("Generate Report")


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
    product = SelectField(
        "Product", coerce=int, validators=[Optional()], validate_choice=False
    )
    unit = SelectField(
        "Unit", coerce=int, validators=[Optional()], validate_choice=False
    )
    quantity = DecimalField("Quantity", validators=[InputRequired()])
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
    unit = SelectField(
        "Unit", coerce=int, validators=[Optional()], validate_choice=False
    )
    quantity = DecimalField("Quantity", validators=[InputRequired()])
    cost = DecimalField("Cost", validators=[InputRequired()])
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
    upc = StringField("UPC", validators=[DataRequired(), Length(max=32)])
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
            FileAllowed({"xls", "pdf"}, "Only .xls or .pdf files are allowed."),
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
    enable_central_supply_imports = BooleanField("CENTRAL SUPPLY", default=True)
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
            ("CENTRAL SUPPLY", self.enable_central_supply_imports),
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
    submit = SubmitField("Update Notifications")


class NoteForm(FlaskForm):
    content = TextAreaField(
        "Note", validators=[DataRequired(), Length(max=2000)]
    )
    pinned = BooleanField("Pin note")
    submit = SubmitField("Save Note")
