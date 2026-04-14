from __future__ import annotations

from datetime import datetime

from flask import (
    Blueprint,
    abort,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    url_for,
)
from flask_login import login_required
from sqlalchemy.orm import selectinload

from app import db
from app.forms import CSRFOnlyForm, DisplayForm, PlaylistForm, PlaylistItemForm
from app.models import Display, Location, Menu, Playlist, PlaylistItem
from app.services.signage import (
    build_display_manifest,
    consume_display_activation_code,
    generate_display_token,
    load_display_for_browser_code,
    load_display_for_activation_code,
    load_display_for_player,
    normalize_activation_code,
    refresh_display_activation_code,
    update_display_heartbeat,
)
from app.utils.activity import log_activity

signage = Blueprint("signage", __name__)


def _ensure_playlist_form_rows(form: PlaylistForm) -> None:
    if form.items.entries:
        return
    form.items.append_entry(
        {
            "source_type": PlaylistItem.SOURCE_LOCATION_MENU,
            "menu_id": 0,
            "duration_seconds": 15,
        }
    )


def _populate_playlist_form(form: PlaylistForm, playlist: Playlist) -> None:
    while form.items.entries:
        form.items.pop_entry()
    for item in playlist.items:
        form.items.append_entry(
            {
                "source_type": item.source_type,
                "menu_id": item.menu_id or 0,
                "duration_seconds": item.duration_seconds,
            }
        )
    _ensure_playlist_form_rows(form)


def _resolve_playlist(form: DisplayForm, field_name: str = "playlist_override_id") -> Playlist | None:
    playlist_id = int(getattr(form, field_name).data or 0)
    if not playlist_id:
        return None
    playlist = db.session.get(Playlist, playlist_id)
    if playlist is None:
        getattr(form, field_name).errors.append(
            "Selected playlist is no longer available."
        )
        return None
    return playlist


def _resolve_location(form: DisplayForm) -> Location | None:
    location = db.session.get(Location, form.location_id.data)
    if location is None:
        form.location_id.errors.append("Selected location is no longer available.")
        return None
    return location


def _build_playlist_items(form: PlaylistForm) -> list[PlaylistItem] | None:
    items: list[PlaylistItem] = []
    for index, item_form in enumerate(form.items.entries):
        source_type = item_form.source_type.data
        menu_id = int(item_form.menu_id.data or 0)
        menu = None
        if source_type == PlaylistItem.SOURCE_MENU:
            menu = db.session.get(Menu, menu_id)
            if menu is None:
                item_form.menu_id.errors.append("Selected menu is no longer available.")
                return None
        items.append(
            PlaylistItem(
                position=index,
                source_type=source_type,
                menu=menu,
                duration_seconds=item_form.duration_seconds.data,
            )
        )
    return items


def _render_player(display: Display):
    return render_template(
        "signage/player.html",
        display=display,
        manifest_url=url_for(
            "signage.player_manifest", public_token=display.public_token
        ),
        heartbeat_url=url_for(
            "signage.player_heartbeat", public_token=display.public_token
        ),
    )


@signage.route("/signage/displays")
@login_required
def view_displays():
    displays = (
        Display.query.options(
            selectinload(Display.location).selectinload(Location.default_playlist),
            selectinload(Display.playlist_override),
        )
        .order_by(Display.archived.asc(), Display.name, Display.id)
        .all()
    )
    action_form = CSRFOnlyForm()
    return render_template(
        "signage/view_displays.html",
        displays=displays,
        action_form=action_form,
    )


@signage.route("/signage/displays/add", methods=["GET", "POST"])
@login_required
def add_display():
    form = DisplayForm()
    if form.validate_on_submit():
        location = _resolve_location(form)
        playlist_override = _resolve_playlist(form)
        if location is not None and not form.playlist_override_id.errors:
            display = Display(
                name=form.name.data,
                location=location,
                playlist_override=playlist_override,
                archived=form.archived.data,
                public_token=generate_display_token(),
            )
            db.session.add(display)
            db.session.commit()
            log_activity(f"Created signage display {display.name}")
            flash("Display created successfully.", "success")
            return redirect(url_for("signage.view_displays"))
    return render_template("signage/edit_display.html", form=form, display=None)


@signage.route("/signage/displays/<int:display_id>/edit", methods=["GET", "POST"])
@login_required
def edit_display(display_id: int):
    display = db.session.get(Display, display_id)
    if display is None:
        abort(404)
    form = DisplayForm(obj=display, obj_id=display.id)
    if request.method == "GET":
        form.location_id.data = display.location_id
        form.playlist_override_id.data = display.playlist_override_id or 0
    if form.validate_on_submit():
        location = _resolve_location(form)
        playlist_override = _resolve_playlist(form)
        if location is not None and not form.playlist_override_id.errors:
            display.name = form.name.data
            display.location = location
            display.playlist_override = playlist_override
            display.archived = form.archived.data
            db.session.commit()
            log_activity(f"Updated signage display {display.name}")
            flash("Display updated successfully.", "success")
            return redirect(url_for("signage.view_displays"))
    return render_template("signage/edit_display.html", form=form, display=display)


@signage.route("/signage/displays/<int:display_id>/archive", methods=["POST"])
@login_required
def toggle_display_archive(display_id: int):
    form = CSRFOnlyForm()
    if not form.validate_on_submit():
        flash("Unable to validate archive request.", "danger")
        return redirect(url_for("signage.view_displays"))
    display = db.session.get(Display, display_id)
    if display is None:
        abort(404)
    display.archived = not display.archived
    db.session.commit()
    log_activity(
        f"{'Archived' if display.archived else 'Restored'} signage display {display.name}"
    )
    flash(
        f"Display {'archived' if display.archived else 'restored'}.",
        "success",
    )
    return redirect(url_for("signage.view_displays"))


@signage.route("/signage/displays/<int:display_id>/regenerate-token", methods=["POST"])
@login_required
def regenerate_display_token(display_id: int):
    form = CSRFOnlyForm()
    if not form.validate_on_submit():
        flash("Unable to validate token reset request.", "danger")
        return redirect(url_for("signage.view_displays"))
    display = db.session.get(Display, display_id)
    if display is None:
        abort(404)
    display.public_token = generate_display_token()
    db.session.commit()
    log_activity(f"Regenerated signage player token for {display.name}")
    flash("Display player URL regenerated.", "success")
    return redirect(url_for("signage.view_displays"))


@signage.route("/signage/displays/<int:display_id>/activation-code", methods=["POST"])
@login_required
def issue_display_activation_code(display_id: int):
    form = CSRFOnlyForm()
    if not form.validate_on_submit():
        flash("Unable to validate activation request.", "danger")
        return redirect(url_for("signage.view_displays"))
    display = db.session.get(Display, display_id)
    if display is None:
        abort(404)
    refresh_display_activation_code(display, lifetime_minutes=30)
    log_activity(f"Issued Tizen activation code for signage display {display.name}")
    flash(
        f"Activation code for {display.name}: {display.activation_code}",
        "success",
    )
    return redirect(url_for("signage.view_displays"))


@signage.route("/signage/playlists")
@login_required
def view_playlists():
    playlists = (
        Playlist.query.options(
            selectinload(Playlist.items).selectinload(PlaylistItem.menu),
            selectinload(Playlist.locations),
            selectinload(Playlist.displays),
        )
        .order_by(Playlist.archived.asc(), Playlist.name, Playlist.id)
        .all()
    )
    action_form = CSRFOnlyForm()
    return render_template(
        "signage/view_playlists.html",
        playlists=playlists,
        action_form=action_form,
    )


@signage.route("/signage/playlists/add", methods=["GET", "POST"])
@login_required
def add_playlist():
    form = PlaylistForm()
    _ensure_playlist_form_rows(form)
    if form.validate_on_submit():
        playlist = Playlist(
            name=form.name.data,
            description=form.description.data,
        )
        items = _build_playlist_items(form)
        if items is not None:
            playlist.items = items
            db.session.add(playlist)
            db.session.commit()
            log_activity(f"Created signage playlist {playlist.name}")
            flash("Playlist created successfully.", "success")
            return redirect(url_for("signage.view_playlists"))
    return render_template(
        "signage/edit_playlist.html",
        form=form,
        playlist=None,
        item_template_form=PlaylistItemForm(prefix="items-__prefix__"),
    )


@signage.route("/signage/playlists/<int:playlist_id>/edit", methods=["GET", "POST"])
@login_required
def edit_playlist(playlist_id: int):
    playlist = (
        Playlist.query.options(selectinload(Playlist.items))
        .filter_by(id=playlist_id)
        .first()
    )
    if playlist is None:
        abort(404)
    form = PlaylistForm(obj=playlist, obj_id=playlist.id)
    if request.method == "GET":
        _populate_playlist_form(form, playlist)
    else:
        _ensure_playlist_form_rows(form)
    if form.validate_on_submit():
        items = _build_playlist_items(form)
        if items is not None:
            playlist.name = form.name.data
            playlist.description = form.description.data
            playlist.items.clear()
            playlist.items.extend(items)
            db.session.commit()
            log_activity(f"Updated signage playlist {playlist.name}")
            flash("Playlist updated successfully.", "success")
            return redirect(url_for("signage.view_playlists"))
    return render_template(
        "signage/edit_playlist.html",
        form=form,
        playlist=playlist,
        item_template_form=PlaylistItemForm(prefix="items-__prefix__"),
    )


@signage.route("/signage/playlists/<int:playlist_id>/archive", methods=["POST"])
@login_required
def toggle_playlist_archive(playlist_id: int):
    form = CSRFOnlyForm()
    if not form.validate_on_submit():
        flash("Unable to validate archive request.", "danger")
        return redirect(url_for("signage.view_playlists"))
    playlist = db.session.get(Playlist, playlist_id)
    if playlist is None:
        abort(404)
    playlist.archived = not playlist.archived
    db.session.commit()
    log_activity(
        f"{'Archived' if playlist.archived else 'Restored'} signage playlist {playlist.name}"
    )
    flash(
        f"Playlist {'archived' if playlist.archived else 'restored'}.",
        "success",
    )
    return redirect(url_for("signage.view_playlists"))


@signage.route("/signage/playlists/<int:playlist_id>/delete", methods=["POST"])
@login_required
def delete_playlist(playlist_id: int):
    form = CSRFOnlyForm()
    if not form.validate_on_submit():
        flash("Unable to validate delete request.", "danger")
        return redirect(url_for("signage.view_playlists"))
    playlist = (
        Playlist.query.options(
            selectinload(Playlist.locations),
            selectinload(Playlist.displays),
        )
        .filter_by(id=playlist_id)
        .first()
    )
    if playlist is None:
        abort(404)
    if playlist.locations or playlist.displays:
        flash("Playlist is still assigned and cannot be deleted.", "danger")
        return redirect(url_for("signage.view_playlists"))
    db.session.delete(playlist)
    db.session.commit()
    log_activity(f"Deleted signage playlist {playlist.name}")
    flash("Playlist deleted successfully.", "success")
    return redirect(url_for("signage.view_playlists"))


@signage.route("/signage/tizen/launcher")
def tizen_launcher():
    if request.args.get("reset") == "1":
        return render_template(
            "signage/tizen_launcher.html",
            reset=True,
            activate_url=url_for("signage.tizen_activate"),
        )
    return render_template(
        "signage/tizen_launcher.html",
        reset=False,
        activate_url=url_for("signage.tizen_activate"),
    )


@signage.route("/api/signage/tizen/activate", methods=["POST"])
def tizen_activate():
    payload = request.get_json(silent=True) or request.form
    submitted_code = normalize_activation_code(payload.get("code"))
    if not submitted_code:
        return jsonify({"ok": False, "error": "Activation code is required."}), 400

    display = load_display_for_activation_code(submitted_code)
    if display is None:
        return jsonify({"ok": False, "error": "Activation code was not found."}), 404
    if (
        display.activation_code_expires_at is None
        or display.activation_code_expires_at < datetime.utcnow()
    ):
        return jsonify({"ok": False, "error": "Activation code has expired."}), 410

    consume_display_activation_code(display)
    return jsonify(
        {
            "ok": True,
            "display": {
                "id": display.id,
                "name": display.name,
                "public_token": display.public_token,
                "browser_code": display.browser_code,
            },
            "player_url": url_for(
                "signage.player_short_page",
                browser_code=display.browser_code,
                _external=True,
            ),
            "manifest_url": url_for(
                "signage.player_manifest",
                public_token=display.public_token,
                _external=True,
            ),
            "heartbeat_url": url_for(
                "signage.player_heartbeat",
                public_token=display.public_token,
                _external=True,
            ),
        }
    )


@signage.route("/s/<browser_code>")
def player_short_page(browser_code: str):
    display = load_display_for_browser_code(browser_code)
    if display is None:
        abort(404)
    return _render_player(display)


@signage.route("/player/<public_token>")
def player_page(public_token: str):
    display = load_display_for_player(public_token)
    if display is None:
        abort(404)
    return _render_player(display)


@signage.route("/api/player/<public_token>/manifest")
def player_manifest(public_token: str):
    display = load_display_for_player(public_token)
    if display is None:
        abort(404)
    return jsonify(build_display_manifest(display))


@signage.route("/api/player/<public_token>/heartbeat", methods=["POST"])
def player_heartbeat(public_token: str):
    display = load_display_for_player(public_token)
    if display is None:
        abort(404)
    update_display_heartbeat(
        display,
        remote_addr=request.headers.get("X-Forwarded-For") or request.remote_addr,
        user_agent=request.headers.get("User-Agent"),
    )
    return jsonify({"ok": True, "last_seen_at": display.last_seen_at.isoformat() + "Z"})
