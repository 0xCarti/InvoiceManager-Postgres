from app.models import Item
from app.utils.text import (
    DEFAULT_TEXT_MATCH_MODE,
    build_text_match_predicate,
    normalize_text_match_mode,
)


def _compiled_sql(predicate) -> str:
    return str(predicate.compile(compile_kwargs={"literal_binds": True}))


def test_normalize_text_match_mode_defaults_to_contains():
    assert normalize_text_match_mode(None) == DEFAULT_TEXT_MATCH_MODE
    assert normalize_text_match_mode("invalid") == DEFAULT_TEXT_MATCH_MODE


def test_build_text_match_predicate_exact_is_case_insensitive():
    predicate = build_text_match_predicate(Item.name, "TeSt", "exact")
    compiled = _compiled_sql(predicate).lower()
    assert "lower(" in compiled
    assert " = " in compiled
    assert "'test'" in compiled


def test_build_text_match_predicate_uses_like_patterns():
    startswith_predicate = build_text_match_predicate(
        Item.name, "abc", "startswith"
    )
    contains_predicate = build_text_match_predicate(Item.name, "abc", "contains")
    not_contains_predicate = build_text_match_predicate(
        Item.name, "abc", "not_contains"
    )

    startswith_sql = _compiled_sql(startswith_predicate)
    contains_sql = _compiled_sql(contains_predicate)
    not_contains_sql = _compiled_sql(not_contains_predicate)

    assert "abc%" in startswith_sql
    assert "%abc%" in contains_sql
    assert "%abc%" in not_contains_sql
