"""Helpers for handling paginated views."""

from __future__ import annotations

from typing import Any, Dict, List, Mapping, Tuple, Union

from flask import request

PAGINATION_SIZES: Tuple[int, ...] = (25, 50, 100, 250, 500, 1000)


def get_per_page(param: str = "per_page", default: int = 25) -> int:
    """Return a validated per-page value from the query string.

    Parameters
    ----------
    param:
        Query string parameter containing the requested page size.
    default:
        Fallback value used when the parameter is missing or invalid.

    Returns
    -------
    int
        A value from :data:`PAGINATION_SIZES`.
    """

    value = request.args.get(param, type=int)
    if value in PAGINATION_SIZES:
        return value
    if default in PAGINATION_SIZES:
        return default
    return PAGINATION_SIZES[0]


def build_pagination_args(
    per_page: int,
    *,
    page_param: str = "page",
    per_page_param: str = "per_page",
    extra_params: Mapping[str, Any] | None = None,
) -> Dict[str, Union[str, List[str]]]:
    """Assemble arguments for pagination links.

    Parameters
    ----------
    per_page:
        The validated per-page value.
    page_param:
        Name of the page number query parameter to exclude.
    per_page_param:
        Name of the per-page query parameter to include.

    Returns
    -------
    dict
        Mapping of query parameter names to values suitable for ``url_for``.
    """

    args: Dict[str, Union[str, List[str]]] = {}
    for key, values in request.args.lists():
        if key in {page_param, per_page_param}:
            continue
        if not values:
            continue
        if len(values) == 1:
            args[key] = values[0]
        else:
            args[key] = values
    args[per_page_param] = str(per_page)
    if extra_params:
        for key, value in extra_params.items():
            if value is None or key in args:
                continue
            if isinstance(value, (list, tuple)):
                args[key] = [str(v) for v in value]
            else:
                args[key] = str(value)
    return args
