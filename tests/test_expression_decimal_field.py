from decimal import Decimal

from flask_wtf import FlaskForm
from werkzeug.datastructures import MultiDict

from app.forms import DecimalField


class DummyExpressionForm(FlaskForm):
    value = DecimalField("Value", places=None)


def test_expression_evaluates_when_prefixed(app):
    with app.test_request_context():
        form = DummyExpressionForm(formdata=MultiDict({"value": "=1000*5"}))
        assert form.validate()
        assert form.value.data == Decimal("5000")


def test_expression_requires_prefix(app):
    with app.test_request_context():
        form = DummyExpressionForm(formdata=MultiDict({"value": "1000*5"}))
        assert not form.validate()
        assert "start the value with '='" in form.value.errors[0]


def test_negative_number_without_prefix_is_allowed(app):
    with app.test_request_context():
        form = DummyExpressionForm(formdata=MultiDict({"value": "-5"}))
        assert form.validate()
        assert form.value.data == Decimal("-5")


def test_expression_field_marks_input_for_numeric_js(app):
    with app.test_request_context():
        form = DummyExpressionForm()
        html = form.value()
        assert 'data-numeric-input="1"' in html
        assert 'inputmode="decimal"' in html


def test_decimal_field_accepts_formatted_numbers(app):
    formatted_values = [
        "1,234.50",
        "1 234.50",  # non-breaking space
        "$1,234.50",
        "€1 234,50",
        "(1,234.50)",
    ]
    with app.test_request_context():
        for text in formatted_values:
            form = DummyExpressionForm(
                formdata=MultiDict({"value": text})
            )
            assert form.validate(), text
            assert form.value.data == Decimal("-1234.50") if text.startswith("(") else Decimal("1234.50")

