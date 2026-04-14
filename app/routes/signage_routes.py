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
from app.forms import (
    BoardTemplateForm,
    BoardTemplateBlockForm,
    CSRFOnlyForm,
    DisplayForm,
    PlaylistForm,
    PlaylistItemForm,
)
from app.models import (
    BoardTemplate,
    BoardTemplateBlock,
    Display,
    Location,
    Menu,
    Playlist,
    PlaylistItem,
)
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


def _ensure_board_template_block_rows(form: BoardTemplateForm) -> None:
    if form.blocks.entries:
        return
    form.blocks.append_entry(
        {
            "block_type": BoardTemplateBlock.TYPE_MENU,
            "width_units": 8,
            "title": "Menu",
            "menu_columns": 2,
            "menu_rows": 4,
            "show_title": True,
            "show_prices": True,
            "show_menu_description": False,
        }
    )
    form.blocks.append_entry(
        {
            "block_type": BoardTemplateBlock.TYPE_TEXT,
            "width_units": 4,
            "title": "Promotions",
            "body": "Add combo callouts, announcements, or custom copy here.",
            "show_title": True,
            "show_prices": False,
            "show_menu_description": False,
        }
    )


def _populate_board_template_form(
    form: BoardTemplateForm, board_template: BoardTemplate
) -> None:
    while form.blocks.entries:
        form.blocks.pop_entry()
    for block in board_template.blocks:
        form.blocks.append_entry(
            {
                "block_type": block.block_type,
                "width_units": block.width_units,
                "title": block.title or "",
                "body": block.body or "",
                "media_url": block.media_url or "",
                "menu_columns": block.menu_columns,
                "menu_rows": block.menu_rows,
                "selected_product_ids": block.selected_product_id_list,
                "show_title": block.show_title,
                "show_prices": block.show_prices,
                "show_menu_description": block.show_menu_description,
            }
        )
    _ensure_board_template_block_rows(form)


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


def _resolve_board_template(
    form: DisplayForm, field_name: str = "board_template_id"
) -> BoardTemplate | None:
    template_id = int(getattr(form, field_name).data or 0)
    if not template_id:
        return None
    template = db.session.get(BoardTemplate, template_id)
    if template is None:
        getattr(form, field_name).errors.append(
            "Selected board template is no longer available."
        )
        return None
    return template


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


def _build_board_template_blocks(
    form: BoardTemplateForm,
) -> list[BoardTemplateBlock]:
    blocks: list[BoardTemplateBlock] = []
    for index, block_form in enumerate(form.blocks.entries):
        block = BoardTemplateBlock(
            position=index,
            block_type=block_form.block_type.data,
            width_units=block_form.width_units.data,
            title=block_form.title.data,
            body=block_form.body.data,
            media_url=block_form.media_url.data,
            menu_columns=block_form.menu_columns.data,
            menu_rows=block_form.menu_rows.data,
            show_title=block_form.show_title.data,
            show_prices=block_form.show_prices.data,
            show_menu_description=block_form.show_menu_description.data,
            selected_product_ids=_serialize_selected_product_ids(
                block_form.selected_product_ids.data
            ),
        )
        blocks.append(block)
    return blocks


def _render_player(display: Display):
    manifest = build_display_manifest(display)
    slides = manifest.get("slides") or []
    initial_slide = slides[0] if slides else None
    initial_playlist_name = (manifest.get("playlist") or {}).get("name") or "Location Menu Fallback"
    initial_layout = manifest.get("layout") or {}
    return render_template(
        "signage/player.html",
        display=display,
        manifest_url=url_for(
            "signage.player_manifest", public_token=display.public_token
        ),
        heartbeat_url=url_for(
            "signage.player_heartbeat", public_token=display.public_token
        ),
        initial_slide=initial_slide,
        initial_playlist_name=initial_playlist_name,
        initial_layout=initial_layout,
    )


def _serialize_selected_product_ids(product_ids: list[int] | None) -> str | None:
    values = [str(int(product_id)) for product_id in (product_ids or []) if int(product_id) > 0]
    return ",".join(values) if values else None


@signage.route("/signage/displays")
@login_required
def view_displays():
    displays = (
        Display.query.options(
            selectinload(Display.location).selectinload(Location.default_playlist),
            selectinload(Display.playlist_override),
            selectinload(Display.board_template),
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
        board_template = _resolve_board_template(form)
        if (
            location is not None
            and not form.playlist_override_id.errors
            and not form.board_template_id.errors
        ):
            display = Display(
                name=form.name.data,
                location=location,
                playlist_override=playlist_override,
                board_template=board_template,
                archived=form.archived.data,
                public_token=generate_display_token(),
                board_columns=form.board_columns.data,
                board_rows=form.board_rows.data,
                show_prices=form.show_prices.data,
                show_menu_description=form.show_menu_description.data,
                selected_product_ids=_serialize_selected_product_ids(
                    form.selected_product_ids.data
                ),
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
        form.board_template_id.data = display.board_template_id or 0
        form.selected_product_ids.data = display.selected_product_id_list
    if form.validate_on_submit():
        location = _resolve_location(form)
        playlist_override = _resolve_playlist(form)
        board_template = _resolve_board_template(form)
        if (
            location is not None
            and not form.playlist_override_id.errors
            and not form.board_template_id.errors
        ):
            display.name = form.name.data
            display.location = location
            display.playlist_override = playlist_override
            display.board_template = board_template
            display.board_columns = form.board_columns.data
            display.board_rows = form.board_rows.data
            display.show_prices = form.show_prices.data
            display.show_menu_description = form.show_menu_description.data
            display.selected_product_ids = _serialize_selected_product_ids(
                form.selected_product_ids.data
            )
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


@signage.route("/signage/board-templates")
@login_required
def view_board_templates():
    templates = (
        BoardTemplate.query.options(
            selectinload(BoardTemplate.displays),
            selectinload(BoardTemplate.blocks),
        )
        .order_by(BoardTemplate.archived.asc(), BoardTemplate.name, BoardTemplate.id)
        .all()
    )
    action_form = CSRFOnlyForm()
    return render_template(
        "signage/view_board_templates.html",
        templates=templates,
        action_form=action_form,
    )


@signage.route("/signage/board-templates/add", methods=["GET", "POST"])
@login_required
def add_board_template():
    form = BoardTemplateForm()
    _ensure_board_template_block_rows(form)
    if form.validate_on_submit():
        template = BoardTemplate(
            name=form.name.data,
            description=form.description.data,
            theme=form.theme.data,
            canvas_width=form.canvas_width.data,
            canvas_height=form.canvas_height.data,
            brand_label=form.brand_label.data,
            brand_name=form.brand_name.data,
            menu_columns=form.menu_columns.data,
            menu_rows=form.menu_rows.data,
            side_panel_position=form.side_panel_position.data,
            side_panel_width_percent=form.side_panel_width_percent.data,
            side_title=form.side_title.data,
            side_body=form.side_body.data,
            side_image_url=form.side_image_url.data,
            footer_text=form.footer_text.data,
            show_prices=form.show_prices.data,
            show_menu_description=form.show_menu_description.data,
            show_page_indicator=form.show_page_indicator.data,
            archived=form.archived.data,
        )
        template.blocks = _build_board_template_blocks(form)
        db.session.add(template)
        db.session.commit()
        log_activity(f"Created signage board template {template.name}")
        flash("Board template created successfully.", "success")
        return redirect(url_for("signage.view_board_templates"))
    return render_template(
        "signage/edit_board_template.html",
        form=form,
        board_template=None,
        item_template_form=BoardTemplateBlockForm(prefix="blocks-__prefix__"),
    )


@signage.route("/signage/board-templates/<int:template_id>/edit", methods=["GET", "POST"])
@login_required
def edit_board_template(template_id: int):
    board_template = (
        BoardTemplate.query.options(selectinload(BoardTemplate.blocks))
        .filter_by(id=template_id)
        .first()
    )
    if board_template is None:
        abort(404)
    form = BoardTemplateForm(obj=board_template, obj_id=board_template.id)
    if request.method == "GET":
        _populate_board_template_form(form, board_template)
    else:
        _ensure_board_template_block_rows(form)
    if form.validate_on_submit():
        board_template.name = form.name.data
        board_template.description = form.description.data
        board_template.theme = form.theme.data
        board_template.canvas_width = form.canvas_width.data
        board_template.canvas_height = form.canvas_height.data
        board_template.brand_label = form.brand_label.data
        board_template.brand_name = form.brand_name.data
        board_template.menu_columns = form.menu_columns.data
        board_template.menu_rows = form.menu_rows.data
        board_template.side_panel_position = form.side_panel_position.data
        board_template.side_panel_width_percent = form.side_panel_width_percent.data
        board_template.side_title = form.side_title.data
        board_template.side_body = form.side_body.data
        board_template.side_image_url = form.side_image_url.data
        board_template.footer_text = form.footer_text.data
        board_template.show_prices = form.show_prices.data
        board_template.show_menu_description = form.show_menu_description.data
        board_template.show_page_indicator = form.show_page_indicator.data
        board_template.archived = form.archived.data
        board_template.blocks.clear()
        board_template.blocks.extend(_build_board_template_blocks(form))
        db.session.commit()
        log_activity(f"Updated signage board template {board_template.name}")
        flash("Board template updated successfully.", "success")
        return redirect(url_for("signage.view_board_templates"))
    return render_template(
        "signage/edit_board_template.html",
        form=form,
        board_template=board_template,
        item_template_form=BoardTemplateBlockForm(prefix="blocks-__prefix__"),
    )


@signage.route("/signage/board-templates/<int:template_id>/archive", methods=["POST"])
@login_required
def toggle_board_template_archive(template_id: int):
    form = CSRFOnlyForm()
    if not form.validate_on_submit():
        flash("Unable to validate archive request.", "danger")
        return redirect(url_for("signage.view_board_templates"))
    board_template = db.session.get(BoardTemplate, template_id)
    if board_template is None:
        abort(404)
    board_template.archived = not board_template.archived
    db.session.commit()
    log_activity(
        f"{'Archived' if board_template.archived else 'Restored'} signage board template {board_template.name}"
    )
    flash(
        f"Board template {'archived' if board_template.archived else 'restored'}.",
        "success",
    )
    return redirect(url_for("signage.view_board_templates"))


@signage.route("/signage/board-templates/<int:template_id>/delete", methods=["POST"])
@login_required
def delete_board_template(template_id: int):
    form = CSRFOnlyForm()
    if not form.validate_on_submit():
        flash("Unable to validate delete request.", "danger")
        return redirect(url_for("signage.view_board_templates"))
    board_template = (
        BoardTemplate.query.options(selectinload(BoardTemplate.displays))
        .filter_by(id=template_id)
        .first()
    )
    if board_template is None:
        abort(404)
    if board_template.displays:
        flash("Board template is still assigned and cannot be deleted.", "danger")
        return redirect(url_for("signage.view_board_templates"))
    db.session.delete(board_template)
    db.session.commit()
    log_activity(f"Deleted signage board template {board_template.name}")
    flash("Board template deleted successfully.", "success")
    return redirect(url_for("signage.view_board_templates"))


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
