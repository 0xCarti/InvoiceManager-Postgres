"""Blueprint providing entity note management views."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Tuple

from flask import Blueprint, abort, flash, redirect, render_template, url_for
from flask_login import current_user, login_required
from sqlalchemy.orm import joinedload

from app import db
from app.forms import CSRFOnlyForm, DeleteForm, NoteForm
from app.models import (
    Customer,
    Invoice,
    Item,
    Location,
    Note,
    Product,
    PurchaseInvoice,
    PurchaseOrder,
    Transfer,
    Vendor,
)
from app.utils.activity import log_activity

notes = Blueprint("notes", __name__)


@dataclass(frozen=True)
class EntityConfig:
    model: type
    label: str
    name_getter: Callable[[Any], str]
    parse_identifier: Callable[[str], Any]
    identifier_getter: Callable[[Any], str]
    back_getter: Callable[[Any], Tuple[str, str]]
    activity_getter: Callable[[Any], str]


def _person_name(person: Any) -> str:
    first = getattr(person, "first_name", "") or ""
    last = getattr(person, "last_name", "") or ""
    full = f"{first} {last}".strip()
    return full or f"#{getattr(person, 'id', '')}"


ENTITY_CONFIG: dict[str, EntityConfig] = {
    "location": EntityConfig(
        model=Location,
        label="Location",
        name_getter=lambda obj: obj.name,
        parse_identifier=lambda raw: int(raw),
        identifier_getter=lambda obj: str(obj.id),
        back_getter=lambda obj: (
            url_for("locations.location_items", location_id=obj.id),
            f"{obj.name} Items",
        ),
        activity_getter=lambda obj: f"location {obj.name}",
    ),
    "item": EntityConfig(
        model=Item,
        label="Item",
        name_getter=lambda obj: obj.name,
        parse_identifier=lambda raw: int(raw),
        identifier_getter=lambda obj: str(obj.id),
        back_getter=lambda obj: (
            url_for("item.view_item", item_id=obj.id),
            obj.name,
        ),
        activity_getter=lambda obj: f"item {obj.name}",
    ),
    "product": EntityConfig(
        model=Product,
        label="Product",
        name_getter=lambda obj: obj.name,
        parse_identifier=lambda raw: int(raw),
        identifier_getter=lambda obj: str(obj.id),
        back_getter=lambda obj: (
            url_for("product.edit_product", product_id=obj.id),
            f"Edit {obj.name}",
        ),
        activity_getter=lambda obj: f"product {obj.name}",
    ),
    "vendor": EntityConfig(
        model=Vendor,
        label="Vendor",
        name_getter=_person_name,
        parse_identifier=lambda raw: int(raw),
        identifier_getter=lambda obj: str(obj.id),
        back_getter=lambda obj: (
            url_for("vendor.edit_vendor", vendor_id=obj.id),
            f"Edit {_person_name(obj)}",
        ),
        activity_getter=lambda obj: f"vendor {_person_name(obj)}",
    ),
    "customer": EntityConfig(
        model=Customer,
        label="Customer",
        name_getter=_person_name,
        parse_identifier=lambda raw: int(raw),
        identifier_getter=lambda obj: str(obj.id),
        back_getter=lambda obj: (
            url_for("customer.edit_customer", customer_id=obj.id),
            f"Edit {_person_name(obj)}",
        ),
        activity_getter=lambda obj: f"customer {_person_name(obj)}",
    ),
    "transfer": EntityConfig(
        model=Transfer,
        label="Transfer",
        name_getter=lambda obj: f"Transfer #{obj.id}",
        parse_identifier=lambda raw: int(raw),
        identifier_getter=lambda obj: str(obj.id),
        back_getter=lambda obj: (
            url_for("transfer.view_transfer", transfer_id=obj.id),
            f"Transfer #{obj.id}",
        ),
        activity_getter=lambda obj: f"transfer {obj.id}",
    ),
    "purchase_order": EntityConfig(
        model=PurchaseOrder,
        label="Purchase Order",
        name_getter=lambda obj: f"PO #{obj.id}",
        parse_identifier=lambda raw: int(raw),
        identifier_getter=lambda obj: str(obj.id),
        back_getter=lambda obj: (
            url_for("purchase.edit_purchase_order", po_id=obj.id),
            f"Edit PO #{obj.id}",
        ),
        activity_getter=lambda obj: f"purchase order {obj.id}",
    ),
    "purchase_invoice": EntityConfig(
        model=PurchaseInvoice,
        label="Purchase Invoice",
        name_getter=lambda obj: obj.invoice_number or f"Invoice #{obj.id}",
        parse_identifier=lambda raw: int(raw),
        identifier_getter=lambda obj: str(obj.id),
        back_getter=lambda obj: (
            url_for("purchase.view_purchase_invoice", invoice_id=obj.id),
            obj.invoice_number or f"Invoice #{obj.id}",
        ),
        activity_getter=lambda obj: (
            f"purchase invoice {obj.invoice_number}" if obj.invoice_number else f"purchase invoice {obj.id}"
        ),
    ),
    "sales_invoice": EntityConfig(
        model=Invoice,
        label="Sales Invoice",
        name_getter=lambda obj: obj.id,
        parse_identifier=lambda raw: raw,
        identifier_getter=lambda obj: obj.id,
        back_getter=lambda obj: (
            url_for("invoice.view_invoice", invoice_id=obj.id),
            f"Invoice {obj.id}",
        ),
        activity_getter=lambda obj: f"sales invoice {obj.id}",
    ),
}


def _get_entity_context(entity_type: str, raw_entity_id: str) -> tuple[EntityConfig, Any, str]:
    config = ENTITY_CONFIG.get(entity_type)
    if config is None:
        abort(404)

    try:
        lookup_id = config.parse_identifier(raw_entity_id)
    except (TypeError, ValueError):
        abort(404)

    entity = db.session.get(config.model, lookup_id)
    if entity is None:
        abort(404)

    identifier = config.identifier_getter(entity)
    return config, entity, identifier


def _ensure_content(form: NoteForm) -> str:
    content = (form.content.data or "").strip()
    if not content:
        form.content.errors.append("Note cannot be empty.")
    return content


def _note_query(entity_type: str, identifier: str):
    return (
        Note.query.options(joinedload(Note.user))
        .filter_by(entity_type=entity_type, entity_id=identifier)
        .order_by(
            Note.pinned.desc(),
            Note.pinned_at.desc(),
            Note.created_at.desc(),
        )
    )


@notes.route("/notes/<entity_type>/<entity_id>", methods=["GET", "POST"])
@login_required
def entity_notes(entity_type: str, entity_id: str):
    config, entity, identifier = _get_entity_context(entity_type, entity_id)
    form = NoteForm()
    delete_form = DeleteForm()
    pin_form = CSRFOnlyForm()
    can_pin = current_user.is_admin

    if form.validate_on_submit():
        content = _ensure_content(form)
        if content:
            note = Note(
                entity_type=entity_type,
                entity_id=identifier,
                user_id=current_user.id,
                content=content,
            )
            if can_pin and form.pinned.data:
                note.set_pinned(True)
            db.session.add(note)
            db.session.commit()
            log_activity(
                f"Added note to {config.activity_getter(entity)}"
            )
            flash("Note added.", "success")
            return redirect(
                url_for(
                    "notes.entity_notes",
                    entity_type=entity_type,
                    entity_id=identifier,
                )
            )

    back_url, back_label = config.back_getter(entity)
    notes_list = _note_query(entity_type, identifier).all()
    return render_template(
        "notes/entity_notes.html",
        entity_label=config.label,
        entity_name=config.name_getter(entity),
        entity_type=entity_type,
        entity_id=identifier,
        notes=notes_list,
        form=form,
        delete_form=delete_form,
        pin_form=pin_form,
        can_pin=can_pin,
        back_url=back_url,
        back_label=back_label,
    )


def _load_note_or_404(
    entity_type: str, raw_entity_id: str, note_id: int
) -> tuple[EntityConfig, Any, str, Note]:
    config, entity, identifier = _get_entity_context(entity_type, raw_entity_id)
    note = Note.query.filter_by(
        id=note_id, entity_type=entity_type, entity_id=identifier
    ).first()
    if note is None:
        abort(404)
    return config, entity, identifier, note


@notes.route(
    "/notes/<entity_type>/<entity_id>/edit/<int:note_id>",
    methods=["GET", "POST"],
)
@login_required
def edit_note(entity_type: str, entity_id: str, note_id: int):
    config, entity, identifier, note = _load_note_or_404(
        entity_type, entity_id, note_id
    )
    if not (current_user.is_admin or note.user_id == current_user.id):
        abort(403)

    form = NoteForm(obj=note)
    can_pin = current_user.is_admin

    if form.validate_on_submit():
        content = _ensure_content(form)
        if content:
            note.content = content
            if can_pin:
                note.set_pinned(bool(form.pinned.data))
            db.session.commit()
            log_activity(
                f"Updated note on {config.activity_getter(entity)}"
            )
            flash("Note updated.", "success")
            return redirect(
                url_for(
                    "notes.entity_notes",
                    entity_type=entity_type,
                    entity_id=identifier,
                )
            )
    else:
        form.content.data = note.content
        form.pinned.data = note.pinned

    back_url, back_label = config.back_getter(entity)
    return render_template(
        "notes/edit_note.html",
        form=form,
        entity_label=config.label,
        entity_name=config.name_getter(entity),
        entity_type=entity_type,
        entity_id=identifier,
        can_pin=can_pin,
        back_url=back_url,
        back_label=back_label,
    )


@notes.route(
    "/notes/<entity_type>/<entity_id>/delete/<int:note_id>",
    methods=["POST"],
)
@login_required
def delete_note(entity_type: str, entity_id: str, note_id: int):
    form = DeleteForm()
    if not form.validate_on_submit():
        abort(400)
    config, entity, identifier, note = _load_note_or_404(
        entity_type, entity_id, note_id
    )
    db.session.delete(note)
    db.session.commit()
    log_activity(
        f"Deleted note from {config.activity_getter(entity)}"
    )
    flash("Note deleted.", "success")
    return redirect(
        url_for(
            "notes.entity_notes",
            entity_type=entity_type,
            entity_id=identifier,
        )
    )


@notes.route(
    "/notes/<entity_type>/<entity_id>/toggle-pin/<int:note_id>",
    methods=["POST"],
)
@login_required
def toggle_pin(entity_type: str, entity_id: str, note_id: int):
    if not current_user.is_admin:
        abort(403)
    form = CSRFOnlyForm()
    if not form.validate_on_submit():
        abort(400)
    config, entity, identifier, note = _load_note_or_404(
        entity_type, entity_id, note_id
    )
    note.set_pinned(not note.pinned)
    db.session.commit()
    action = "Pinned" if note.pinned else "Unpinned"
    log_activity(f"{action} note on {config.activity_getter(entity)}")
    flash(f"Note {action.lower()}.", "success")
    return redirect(
        url_for(
            "notes.entity_notes",
            entity_type=entity_type,
            entity_id=identifier,
        )
    )
