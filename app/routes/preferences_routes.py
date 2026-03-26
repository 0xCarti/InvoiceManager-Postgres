"""Routes for managing user-specific preferences."""

from __future__ import annotations

from collections.abc import Mapping

from flask import Blueprint, jsonify, request
from flask_login import current_user, login_required

from app.utils.filter_state import normalize_filters, set_filter_defaults

preferences = Blueprint("preferences", __name__, url_prefix="/preferences")


@preferences.route("/filters", methods=["POST"])
@login_required
def save_filter_preferences():
    """Persist saved filter defaults for the authenticated user."""

    payload: Mapping[str, object]
    values_source: Mapping[str, object] | None = None

    if request.is_json:
        payload = request.get_json(silent=True) or {}
        if not isinstance(payload, Mapping):
            return jsonify({"error": "Invalid JSON payload."}), 400
        scope = str(payload.get("scope") or "").strip()
        raw_values = payload.get("values") or {}
        if not isinstance(raw_values, Mapping):
            return jsonify({"error": "'values' must be an object."}), 400
        values_source = raw_values
        exclude = ("page", "reset")
    else:
        payload = request.form
        scope = str(payload.get("scope", "")).strip()
        values_source = payload
        exclude = ("page", "reset", "csrf_token", "scope")

    if not scope:
        return jsonify({"error": "Missing preference scope."}), 400

    normalized = normalize_filters(values_source, exclude=exclude)
    stored = set_filter_defaults(
        current_user,
        scope,
        normalized,
    )
    return jsonify({"scope": scope, "values": stored}), 200
