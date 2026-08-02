"""Utility helpers shared across the test-suite."""

from __future__ import annotations

from io import BytesIO
import re
from typing import Any

from openpyxl import Workbook


_CSRF_RE = re.compile(r'name=["\']csrf_token["\'][^>]*value=["\']([^"\']+)["\']', re.IGNORECASE)


def extract_csrf_token(response: Any, *, required: bool = True) -> str:
    """Return the first CSRF token found in ``response`` HTML content."""

    if hasattr(response, "data"):
        html: str = response.data.decode("utf-8")
    elif isinstance(response, (bytes, bytearray)):
        html = response.decode("utf-8")
    else:
        html = str(response)
    match = _CSRF_RE.search(html)
    if not match:
        if required:
            raise AssertionError("CSRF token not found in response")
        return ""
    return match.group(1)


def login(client, email: str, password: str):
    """Helper to login a user in tests, respecting CSRF protection."""

    login_page = client.get("/auth/login")
    token = extract_csrf_token(login_page, required=False)
    form_data = {"email": email, "password": password}
    if token:
        form_data["csrf_token"] = token
    return client.post(
        "/auth/login",
        data=form_data,
        follow_redirects=True,
    )


def save_filter_defaults(client, scope: str, values: dict[str, list[str]], *, token_path: str = "/items"):
    """Persist filter defaults for ``scope`` via the preferences endpoint."""

    token_response = client.get(token_path, follow_redirects=True)
    token = extract_csrf_token(token_response, required=False)
    headers = {"X-CSRFToken": token} if token else {}
    response = client.post(
        "/preferences/filters",
        json={"scope": scope, "values": values},
        headers=headers,
    )
    if response.status_code != 200:
        raise AssertionError(
            f"Failed to store defaults for {scope!r}: {response.status_code} {response.data!r}"
    )
    return response


def build_terminal_sales_workbook_bytes() -> bytes:
    """Return a small IdealPOS-style workbook with one location and one sale row."""

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Sales"
    sheet.append(["Main Stand"])
    sheet.append(["1001", "Hot Dog", 4.50, None, 2, 9.00, None, 9.00, 0.00])
    sheet.append([None, None, None, None, 2, 9.00, None, 9.00, 0.00])

    output = BytesIO()
    workbook.save(output)
    return output.getvalue()
