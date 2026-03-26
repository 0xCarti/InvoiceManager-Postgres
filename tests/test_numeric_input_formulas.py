import json
import math
from pathlib import Path

from py_mini_racer import py_mini_racer

from app.utils.numeric import coerce_float


ROOT = Path(__file__).resolve().parents[1]


def _load_numeric_input_context():
    ctx = py_mini_racer.MiniRacer()
    ctx.eval(
        """
        var window = {};
        var document = {
            readyState: 'complete',
            addEventListener: function () {},
            querySelectorAll: function () { return []; },
            documentElement: {}
        };
        window.document = document;
        window.HTMLInputElement = function () {};
        window.Element = function () {};
        function MutationObserver(callback) {
            this.observe = function () {};
        }
        window.MutationObserver = MutationObserver;
        var MutationObserver = window.MutationObserver;
        var global = window;
        var globalThis = window;
        """
    )
    ctx.eval(
        """
        if (typeof Number.isFinite !== 'function') {
            Number.isFinite = function (value) { return isFinite(value); };
        }
        if (typeof Number.isInteger !== 'function') {
            Number.isInteger = function (value) {
                return typeof value === 'number' && isFinite(value) && Math.floor(value) === value;
            };
        }
        """
    )
    script_path = ROOT / "app/static/js/numeric_inputs.js"
    ctx.eval(script_path.read_text(encoding="utf-8"))
    return ctx


def test_parse_value_supports_expression_without_equals():
    ctx = _load_numeric_input_context()
    result = ctx.eval('window.NumericInput.parseValue("1+2*3")')
    assert result == 7


def test_parse_value_keeps_plain_numbers_unchanged():
    ctx = _load_numeric_input_context()
    assert ctx.eval('window.NumericInput.parseValue("42")') == 42


def test_parse_value_returns_nan_for_invalid_tokens():
    ctx = _load_numeric_input_context()
    value = ctx.eval('window.NumericInput.parseValue("1+foo")')
    assert isinstance(value, float) and math.isnan(value)


def test_parse_value_returns_nan_for_date_like_strings():
    ctx = _load_numeric_input_context()
    date_values = [
        "2024-07-01",
        "07/01/2024",
        "2024/07/01",
    ]
    for date_value in date_values:
        result = ctx.eval(
            f'window.NumericInput.parseValue({json.dumps(date_value)})'
        )
        assert isinstance(result, float) and math.isnan(result)


def test_parse_value_does_not_mutate_inputs_for_date_like_strings():
    ctx = _load_numeric_input_context()
    date_values = [
        "2024-07-01",
        "07/01/2024",
        "2024/07/01",
    ]
    for date_value in date_values:
        ctx.eval(
            f"""
            (function () {{
              var input = new window.HTMLInputElement();
              input.value = {json.dumps(date_value)};
              window.__dateParseResult = window.NumericInput.parseValue(input);
              window.__dateValueAfterParse = input.value;
            }})()
            """
        )
        assert ctx.eval('isNaN(window.__dateParseResult)')
        assert ctx.eval('window.__dateValueAfterParse') == date_value


def test_coerce_float_supports_comma_decimal_separator():
    assert coerce_float("1,0000") == 1.0

