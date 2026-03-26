from app.utils.pagination import (
    PAGINATION_SIZES,
    build_pagination_args,
    get_per_page,
)

def test_get_per_page_defaults(app):
    with app.test_request_context("/"):
        assert get_per_page() == PAGINATION_SIZES[0]


def test_get_per_page_accepts_allowed_values(app):
    with app.test_request_context("/?per_page=100"):
        assert get_per_page() == 100


def test_get_per_page_rejects_invalid_values(app):
    with app.test_request_context("/?per_page=7"):
        assert get_per_page() == PAGINATION_SIZES[0]


def test_get_per_page_with_custom_parameter(app):
    with app.test_request_context("/?purchase_per_page=250"):
        assert get_per_page("purchase_per_page") == 250


def test_build_pagination_args_includes_other_params(app):
    url = "/?per_page=50&page=3&name_query=test&filter=active"
    with app.test_request_context(url):
        args = build_pagination_args(50)
    assert args["per_page"] == "50"
    assert args["name_query"] == "test"
    assert args["filter"] == "active"
    assert "page" not in args


def test_build_pagination_args_preserves_list_values(app):
    url = "/?per_page=25&page=2&gl_code_id=1&gl_code_id=2"
    with app.test_request_context(url):
        args = build_pagination_args(25)
    assert args["gl_code_id"] == ["1", "2"]


def test_build_pagination_args_with_custom_names(app):
    url = (
        "/?purchase_page=4&purchase_per_page=500&sales_page=2&sales_per_page=50"
    )
    with app.test_request_context(url):
        args = build_pagination_args(
            500, page_param="purchase_page", per_page_param="purchase_per_page"
        )
    assert args["purchase_per_page"] == "500"
    assert "purchase_page" not in args
    # Other pagination controls remain untouched
    assert args["sales_page"] == "2"
    assert args["sales_per_page"] == "50"


def test_build_pagination_args_with_extra_params(app):
    with app.test_request_context("/?page=3"):
        args = build_pagination_args(25, extra_params={"archived": "active"})
    assert args["archived"] == "active"
    assert "page" not in args

    with app.test_request_context("/?archived=archived"):
        args = build_pagination_args(25, extra_params={"archived": "active"})
    assert args["archived"] == "archived"
