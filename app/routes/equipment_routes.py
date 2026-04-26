from __future__ import annotations

from datetime import date as date_cls, datetime, timedelta

from flask import (
    Blueprint,
    Response,
    abort,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    url_for,
)
from flask_login import current_user, login_required
from sqlalchemy import case, func, or_
from sqlalchemy.orm import selectinload

from app import db
from app.forms import (
    DeleteForm,
    EquipmentAssetForm,
    EquipmentCategoryForm,
    EquipmentIntakeBatchForm,
    EquipmentIntakeReceiveForm,
    EquipmentMaintenanceIssueForm,
    EquipmentMaintenanceUpdateForm,
    EquipmentModelForm,
    EquipmentSnipeItImportForm,
)
from app.models import (
    EquipmentAsset,
    EquipmentCategory,
    EquipmentIntakeBatch,
    EquipmentMaintenanceIssue,
    EquipmentMaintenanceUpdate,
    EquipmentModel,
    Location,
    PurchaseInvoice,
    PurchaseOrder,
    User,
    Vendor,
)
from app.services.equipment_imports import EquipmentImportError, run_snipe_it_import
from app.services.equipment_labels import render_equipment_label_pdf
from app.utils.activity import log_activity
from app.utils.filter_state import (
    filters_to_query_args,
    get_filter_defaults,
    normalize_filters,
)
from app.utils.pagination import build_pagination_args, get_per_page
from app.utils.text import (
    build_text_match_predicate,
    normalize_request_text_filter,
    normalize_text_match_mode,
)

equipment = Blueprint("equipment", __name__)


def _vendor_name(vendor) -> str:
    if vendor is None:
        return ""
    return f"{vendor.first_name} {vendor.last_name}".strip()


def _load_asset_or_404(asset_id: int) -> EquipmentAsset:
    asset = (
        EquipmentAsset.query.options(
            selectinload(EquipmentAsset.equipment_model).selectinload(
                EquipmentModel.category
            ),
            selectinload(EquipmentAsset.intake_batch).selectinload(
                EquipmentIntakeBatch.equipment_model
            ),
            selectinload(EquipmentAsset.intake_batch).selectinload(
                EquipmentIntakeBatch.purchase_vendor
            ),
            selectinload(EquipmentAsset.intake_batch).selectinload(
                EquipmentIntakeBatch.purchase_order
            ),
            selectinload(EquipmentAsset.intake_batch).selectinload(
                EquipmentIntakeBatch.purchase_invoice
            ),
            selectinload(EquipmentAsset.purchase_vendor),
            selectinload(EquipmentAsset.service_vendor),
            selectinload(EquipmentAsset.location),
            selectinload(EquipmentAsset.assigned_user),
        )
        .filter(EquipmentAsset.id == asset_id)
        .one_or_none()
    )
    if asset is None:
        abort(404)
    return asset


def _load_intake_batch_or_404(batch_id: int) -> EquipmentIntakeBatch:
    batch = (
        EquipmentIntakeBatch.query.options(
            selectinload(EquipmentIntakeBatch.equipment_model).selectinload(
                EquipmentModel.category
            ),
            selectinload(EquipmentIntakeBatch.purchase_vendor),
            selectinload(EquipmentIntakeBatch.purchase_order).selectinload(
                PurchaseOrder.vendor
            ),
            selectinload(EquipmentIntakeBatch.purchase_invoice).selectinload(
                PurchaseInvoice.purchase_order
            ),
            selectinload(EquipmentIntakeBatch.location),
            selectinload(EquipmentIntakeBatch.assigned_user),
            selectinload(EquipmentIntakeBatch.created_by),
            selectinload(EquipmentIntakeBatch.assets).selectinload(
                EquipmentAsset.location
            ),
            selectinload(EquipmentIntakeBatch.assets).selectinload(
                EquipmentAsset.assigned_user
            ),
        )
        .filter(EquipmentIntakeBatch.id == batch_id)
        .one_or_none()
    )
    if batch is None:
        abort(404)
    return batch


def _load_issue_or_404(issue_id: int) -> EquipmentMaintenanceIssue:
    issue = (
        EquipmentMaintenanceIssue.query.options(
            selectinload(EquipmentMaintenanceIssue.equipment_asset).selectinload(
                EquipmentAsset.equipment_model
            ),
            selectinload(EquipmentMaintenanceIssue.equipment_asset).selectinload(
                EquipmentAsset.location
            ),
            selectinload(EquipmentMaintenanceIssue.assigned_user),
            selectinload(EquipmentMaintenanceIssue.assigned_vendor),
            selectinload(EquipmentMaintenanceIssue.created_by),
            selectinload(EquipmentMaintenanceIssue.updates).selectinload(
                EquipmentMaintenanceUpdate.user
            ),
        )
        .filter(EquipmentMaintenanceIssue.id == issue_id)
        .one_or_none()
    )
    if issue is None:
        abort(404)
    return issue


def _ordered_equipment_assets(ids: list[int]) -> list[EquipmentAsset]:
    assets = (
        EquipmentAsset.query.options(
            selectinload(EquipmentAsset.equipment_model).selectinload(
                EquipmentModel.category
            ),
            selectinload(EquipmentAsset.location),
            selectinload(EquipmentAsset.assigned_user),
        )
        .filter(EquipmentAsset.id.in_(ids))
        .all()
    )
    asset_map = {asset.id: asset for asset in assets}
    ordered_assets = []
    for asset_id in ids:
        asset = asset_map.get(asset_id)
        if asset is None:
            abort(404)
        ordered_assets.append(asset)
    return ordered_assets


def _apply_intake_batch_form(
    batch: EquipmentIntakeBatch, form: EquipmentIntakeBatchForm
) -> None:
    batch.equipment_model_id = form.equipment_model_id.data
    batch.source_type = form.source_type.data
    batch.expected_quantity = int(form.expected_quantity.data or 1)
    batch.unit_cost = (
        float(form.unit_cost.data) if form.unit_cost.data is not None else None
    )
    batch.purchase_vendor_id = form.purchase_vendor_id.data or None
    batch.vendor_name = (form.vendor_name.data or "").strip() or None
    batch.purchase_order_id = form.purchase_order_id.data or None
    batch.purchase_order_reference = (
        (form.purchase_order_reference.data or "").strip() or None
    )
    batch.purchase_invoice_id = form.purchase_invoice_id.data or None
    batch.purchase_invoice_reference = (
        (form.purchase_invoice_reference.data or "").strip() or None
    )
    batch.order_date = form.order_date.data
    batch.expected_received_on = form.expected_received_on.data
    batch.received_on = form.received_on.data
    batch.location_id = form.location_id.data or None
    batch.assigned_user_id = form.assigned_user_id.data or None
    batch.notes = (form.notes.data or "").strip() or None

    purchase_order = (
        db.session.get(PurchaseOrder, batch.purchase_order_id)
        if batch.purchase_order_id
        else None
    )
    purchase_invoice = (
        db.session.get(PurchaseInvoice, batch.purchase_invoice_id)
        if batch.purchase_invoice_id
        else None
    )

    if purchase_invoice is not None and purchase_order is None:
        purchase_order = purchase_invoice.purchase_order
        batch.purchase_order_id = purchase_invoice.purchase_order_id

    if purchase_order is not None:
        if batch.purchase_vendor_id is None and purchase_order.vendor_id:
            batch.purchase_vendor_id = purchase_order.vendor_id
        if batch.vendor_name is None and purchase_order.vendor_name:
            batch.vendor_name = purchase_order.vendor_name
        if batch.purchase_order_reference is None:
            batch.purchase_order_reference = (
                purchase_order.order_number or f"PO #{purchase_order.id}"
            )
        if batch.order_date is None:
            batch.order_date = purchase_order.order_date
        if batch.expected_received_on is None:
            batch.expected_received_on = purchase_order.expected_date

    if purchase_invoice is not None:
        if batch.purchase_vendor_id is None and purchase_invoice.purchase_order:
            batch.purchase_vendor_id = purchase_invoice.purchase_order.vendor_id
        if batch.vendor_name is None and purchase_invoice.vendor_name:
            batch.vendor_name = purchase_invoice.vendor_name
        if batch.purchase_invoice_reference is None:
            batch.purchase_invoice_reference = (
                purchase_invoice.invoice_number
                or f"Invoice #{purchase_invoice.id}"
            )
        if batch.received_on is None:
            batch.received_on = purchase_invoice.received_date
        if batch.location_id is None:
            batch.location_id = purchase_invoice.location_id

    batch.sync_status()


def _apply_asset_form(asset: EquipmentAsset, form: EquipmentAssetForm) -> None:
    asset.equipment_model_id = form.equipment_model_id.data
    asset.name = (form.name.data or "").strip() or None
    asset.asset_tag = (form.asset_tag.data or "").strip()
    asset.serial_number = (form.serial_number.data or "").strip() or None
    asset.status = form.status.data
    asset.description = (form.description.data or "").strip() or None
    asset.acquired_on = form.acquired_on.data
    asset.warranty_expires_on = form.warranty_expires_on.data
    asset.cost = float(form.cost.data) if form.cost.data is not None else None
    asset.purchase_vendor_id = form.purchase_vendor_id.data or None
    asset.service_vendor_id = form.service_vendor_id.data or None
    asset.service_contact_name = (
        (form.service_contact_name.data or "").strip() or None
    )
    asset.service_contact_email = (
        (form.service_contact_email.data or "").strip() or None
    )
    asset.service_contact_phone = (
        (form.service_contact_phone.data or "").strip() or None
    )
    asset.service_contract_name = (
        (form.service_contract_name.data or "").strip() or None
    )
    asset.service_contract_reference = (
        (form.service_contract_reference.data or "").strip() or None
    )
    asset.service_contract_expires_on = form.service_contract_expires_on.data
    asset.service_contract_notes = (
        (form.service_contract_notes.data or "").strip() or None
    )
    asset.service_interval_days = form.service_interval_days.data or None
    asset.last_service_on = form.last_service_on.data
    asset.next_service_due_on = form.next_service_due_on.data
    if (
        asset.next_service_due_on is None
        and asset.last_service_on is not None
        and asset.service_interval_days
    ):
        asset.next_service_due_on = asset.last_service_on + timedelta(
            days=int(asset.service_interval_days)
        )
    asset.location_id = form.location_id.data or None
    asset.sublocation = (form.sublocation.data or "").strip() or None
    asset.assigned_user_id = form.assigned_user_id.data or None


def _materialize_received_asset_rows(
    form: EquipmentIntakeReceiveForm,
) -> list[dict[str, str | None]]:
    rows: list[dict[str, str | None]] = []
    if form.parsed_asset_rows:
        for row in form.parsed_asset_rows:
            rows.append(
                {
                    "asset_tag": (row.get("asset_tag") or "").strip() or None,
                    "serial_number": (row.get("serial_number") or "").strip() or None,
                    "name": (row.get("name") or "").strip() or None,
                    "sublocation": (row.get("sublocation") or "").strip() or None,
                }
            )
        return rows

    width = form.number_width.data or 3
    start = form.starting_number.data or 1
    prefix = (form.asset_tag_prefix.data or "").strip()
    name_prefix = (form.name_prefix.data or "").strip() or None
    for offset in range(int(form.quantity.data or 0)):
        sequence_value = start + offset
        rows.append(
            {
                "asset_tag": f"{prefix}{str(sequence_value).zfill(width)}",
                "serial_number": None,
                "name": (
                    f"{name_prefix} {sequence_value}"
                    if name_prefix
                    else None
                ),
                "sublocation": None,
            }
        )
    return rows


def _apply_issue_status(
    issue: EquipmentMaintenanceIssue, new_status: str
) -> tuple[str, bool]:
    previous_status = issue.status
    reopened = (
        previous_status in {
            EquipmentMaintenanceIssue.STATUS_RESOLVED,
            EquipmentMaintenanceIssue.STATUS_CANCELLED,
        }
        and new_status in EquipmentMaintenanceIssue.OPEN_STATUSES
    )
    issue.status = new_status
    if new_status == EquipmentMaintenanceIssue.STATUS_RESOLVED:
        issue.resolved_on = issue.resolved_on or date_cls.today()
        if issue.downtime_started_on and issue.downtime_resolved_on is None:
            issue.downtime_resolved_on = issue.resolved_on
    elif new_status == EquipmentMaintenanceIssue.STATUS_CANCELLED:
        issue.resolved_on = issue.resolved_on or date_cls.today()
    else:
        issue.resolved_on = None
        if reopened:
            issue.reopened_count = int(issue.reopened_count or 0) + 1
    return previous_status, reopened


def _apply_issue_form(
    issue: EquipmentMaintenanceIssue,
    form: EquipmentMaintenanceIssueForm,
) -> tuple[str, bool]:
    issue.equipment_asset_id = form.equipment_asset_id.data
    issue.title = (form.title.data or "").strip()
    issue.description = (form.description.data or "").strip() or None
    issue.priority = form.priority.data
    issue.reported_on = form.reported_on.data
    issue.due_on = form.due_on.data
    issue.assigned_user_id = form.assigned_user_id.data or None
    issue.assigned_vendor_id = form.assigned_vendor_id.data or None
    issue.parts_cost = (
        float(form.parts_cost.data) if form.parts_cost.data is not None else None
    )
    issue.labor_cost = (
        float(form.labor_cost.data) if form.labor_cost.data is not None else None
    )
    issue.downtime_started_on = form.downtime_started_on.data
    issue.downtime_resolved_on = form.downtime_resolved_on.data
    issue.resolution_summary = (
        (form.resolution_summary.data or "").strip() or None
    )
    issue.resolved_on = form.resolved_on.data
    previous_status, reopened = _apply_issue_status(issue, form.status.data)
    return previous_status, reopened


def _record_issue_update(
    issue: EquipmentMaintenanceIssue,
    *,
    event_type: str,
    message: str | None = None,
    previous_status: str | None = None,
    new_status: str | None = None,
) -> None:
    db.session.add(
        EquipmentMaintenanceUpdate(
            issue=issue,
            user_id=current_user.id,
            event_type=event_type,
            message=(message or "").strip() or None,
            previous_status=previous_status,
            new_status=new_status,
        )
    )


def _open_issue_count_subquery():
    return (
        db.session.query(
            EquipmentMaintenanceIssue.equipment_asset_id.label("asset_id"),
            func.count(EquipmentMaintenanceIssue.id).label("open_issue_count"),
        )
        .filter(
            EquipmentMaintenanceIssue.status.in_(
                tuple(EquipmentMaintenanceIssue.OPEN_STATUSES)
            )
        )
        .group_by(EquipmentMaintenanceIssue.equipment_asset_id)
        .subquery()
    )


@equipment.route("/equipment")
@login_required
def view_equipment():
    scope = request.endpoint or "equipment.view_equipment"
    default_filters = get_filter_defaults(current_user, scope)
    active_filters = normalize_filters(
        request.args, exclude=("page", "per_page", "reset")
    )
    if request.args.get("reset"):
        return redirect(url_for("equipment.view_equipment"))
    if default_filters and not active_filters:
        return redirect(
            url_for(
                "equipment.view_equipment",
                **filters_to_query_args(default_filters),
            )
        )

    today = date_cls.today()
    reminder_cutoff = today + timedelta(days=EquipmentAsset.REMINDER_WINDOW_DAYS)
    page = request.args.get("page", 1, type=int)
    per_page = get_per_page()
    search_query = normalize_request_text_filter(
        request.args.get("search_query")
    )
    match_mode = normalize_text_match_mode(request.args.get("match_mode"))
    status = (request.args.get("status") or "all").strip().lower()
    archived = (request.args.get("archived") or "active").strip().lower()
    category_id = request.args.get("category_id", type=int)
    model_id = request.args.get("model_id", type=int)
    purchase_vendor_id = request.args.get("purchase_vendor_id", type=int)
    location_id = request.args.get("location_id", type=int)
    assigned_user_id = request.args.get("assigned_user_id", type=int)
    attention_state = (request.args.get("attention_state") or "all").strip().lower()

    valid_statuses = {code for code, _label in EquipmentAsset.STATUS_CHOICES}
    if status not in valid_statuses | {"all"}:
        status = "all"
    if archived not in {"active", "archived", "all"}:
        archived = "active"
    if attention_state not in {"all", "needs_attention", "clear"}:
        attention_state = "all"

    open_issue_counts = _open_issue_count_subquery()
    attention_clause = or_(
        func.coalesce(open_issue_counts.c.open_issue_count, 0) > 0,
        EquipmentAsset.warranty_expires_on <= reminder_cutoff,
        EquipmentAsset.service_contract_expires_on <= reminder_cutoff,
        EquipmentAsset.next_service_due_on <= reminder_cutoff,
    )

    query = (
        EquipmentAsset.query.options(
            selectinload(EquipmentAsset.equipment_model).selectinload(
                EquipmentModel.category
            ),
            selectinload(EquipmentAsset.purchase_vendor),
            selectinload(EquipmentAsset.location),
            selectinload(EquipmentAsset.assigned_user),
        )
        .join(EquipmentModel, EquipmentModel.id == EquipmentAsset.equipment_model_id)
        .join(
            EquipmentCategory,
            EquipmentCategory.id == EquipmentModel.category_id,
        )
        .outerjoin(open_issue_counts, open_issue_counts.c.asset_id == EquipmentAsset.id)
    )

    if archived == "active":
        query = query.filter(EquipmentAsset.archived.is_(False))
    elif archived == "archived":
        query = query.filter(EquipmentAsset.archived.is_(True))

    if search_query:
        query = query.filter(
            or_(
                build_text_match_predicate(
                    EquipmentAsset.asset_tag, search_query, match_mode
                ),
                build_text_match_predicate(
                    EquipmentAsset.serial_number, search_query, match_mode
                ),
                build_text_match_predicate(
                    EquipmentAsset.name, search_query, match_mode
                ),
                build_text_match_predicate(
                    EquipmentModel.manufacturer, search_query, match_mode
                ),
                build_text_match_predicate(
                    EquipmentModel.name, search_query, match_mode
                ),
                build_text_match_predicate(
                    EquipmentModel.model_number, search_query, match_mode
                ),
                build_text_match_predicate(
                    EquipmentCategory.name, search_query, match_mode
                ),
            )
        )

    if status != "all":
        query = query.filter(EquipmentAsset.status == status)
    if category_id:
        query = query.filter(EquipmentCategory.id == category_id)
    if model_id:
        query = query.filter(EquipmentAsset.equipment_model_id == model_id)
    if purchase_vendor_id:
        query = query.filter(
            EquipmentAsset.purchase_vendor_id == purchase_vendor_id
        )
    if location_id:
        query = query.filter(EquipmentAsset.location_id == location_id)
    if assigned_user_id:
        query = query.filter(
            EquipmentAsset.assigned_user_id == assigned_user_id
        )
    if attention_state == "needs_attention":
        query = query.filter(attention_clause)
    elif attention_state == "clear":
        query = query.filter(~attention_clause)

    assets = query.order_by(EquipmentAsset.asset_tag.asc()).paginate(
        page=page, per_page=per_page
    )
    delete_form = DeleteForm()

    page_asset_ids = [asset.id for asset in assets.items]
    open_issue_count_map: dict[int, int] = {}
    if page_asset_ids:
        open_issue_count_rows = (
            db.session.query(
                EquipmentMaintenanceIssue.equipment_asset_id,
                func.count(EquipmentMaintenanceIssue.id),
            )
            .filter(
                EquipmentMaintenanceIssue.equipment_asset_id.in_(page_asset_ids),
                EquipmentMaintenanceIssue.status.in_(
                    tuple(EquipmentMaintenanceIssue.OPEN_STATUSES)
                ),
            )
            .group_by(EquipmentMaintenanceIssue.equipment_asset_id)
            .all()
        )
        open_issue_count_map = {
            asset_id: issue_count
            for asset_id, issue_count in open_issue_count_rows
        }

    return render_template(
        "equipment/view_equipment.html",
        assets=assets,
        delete_form=delete_form,
        search_query=search_query,
        match_mode=match_mode,
        status=status,
        archived=archived,
        category_id=category_id,
        model_id=model_id,
        purchase_vendor_id=purchase_vendor_id,
        location_id=location_id,
        assigned_user_id=assigned_user_id,
        attention_state=attention_state,
        open_issue_count_map=open_issue_count_map,
        categories=EquipmentCategory.query.order_by(EquipmentCategory.name).all(),
        models=(
            EquipmentModel.query.options(selectinload(EquipmentModel.category))
            .order_by(
                EquipmentModel.manufacturer.asc(),
                EquipmentModel.name.asc(),
                EquipmentModel.model_number.asc(),
            )
            .all()
        ),
        vendors=Vendor.query.order_by(Vendor.first_name.asc(), Vendor.last_name.asc()).all(),
        locations=Location.query.order_by(Location.name.asc()).all(),
        users=sorted(
            User.query.filter_by(active=True).all(),
            key=lambda user: (user.sort_key, user.email.casefold()),
        ),
        status_choices=EquipmentAsset.STATUS_CHOICES,
        selected_category=db.session.get(EquipmentCategory, category_id)
        if category_id
        else None,
        selected_model=db.session.get(EquipmentModel, model_id) if model_id else None,
        selected_vendor=db.session.get(Vendor, purchase_vendor_id)
        if purchase_vendor_id
        else None,
        selected_location=db.session.get(Location, location_id) if location_id else None,
        selected_user=db.session.get(User, assigned_user_id)
        if assigned_user_id
        else None,
        per_page=per_page,
        pagination_args=build_pagination_args(per_page),
    )


@equipment.route("/equipment/create", methods=["GET", "POST"])
@login_required
def create_equipment_asset():
    form = EquipmentAssetForm()
    if form.validate_on_submit():
        asset = EquipmentAsset()
        _apply_asset_form(asset, form)
        db.session.add(asset)
        db.session.commit()
        log_activity(f"Created equipment {asset.asset_tag}")
        flash("Equipment asset created.", "success")
        return redirect(
            url_for("equipment.view_equipment_asset", asset_id=asset.id)
        )
    return render_template(
        "equipment/asset_form_page.html",
        form=form,
        title="Add Equipment",
        subtitle="Track a physical asset, its ownership, where it lives, and when it needs service.",
    )


@equipment.route("/equipment/<int:asset_id>")
@login_required
def view_equipment_asset(asset_id: int):
    asset = _load_asset_or_404(asset_id)
    delete_form = DeleteForm()
    recent_issues = (
        EquipmentMaintenanceIssue.query.options(
            selectinload(EquipmentMaintenanceIssue.assigned_user),
            selectinload(EquipmentMaintenanceIssue.assigned_vendor),
        )
        .filter(EquipmentMaintenanceIssue.equipment_asset_id == asset.id)
        .order_by(EquipmentMaintenanceIssue.created_at.desc())
        .limit(8)
        .all()
    )
    open_issue_count = (
        EquipmentMaintenanceIssue.query.filter(
            EquipmentMaintenanceIssue.equipment_asset_id == asset.id,
            EquipmentMaintenanceIssue.status.in_(
                tuple(EquipmentMaintenanceIssue.OPEN_STATUSES)
            ),
        ).count()
    )
    return render_template(
        "equipment/view_asset.html",
        asset=asset,
        delete_form=delete_form,
        purchase_vendor_name=_vendor_name(asset.purchase_vendor),
        service_vendor_name=_vendor_name(asset.service_vendor),
        recent_issues=recent_issues,
        open_issue_count=open_issue_count,
    )


@equipment.route("/equipment/<int:asset_id>/edit", methods=["GET", "POST"])
@login_required
def edit_equipment_asset(asset_id: int):
    asset = _load_asset_or_404(asset_id)
    form = EquipmentAssetForm(obj=asset, obj_id=asset.id)

    if request.method == "GET":
        form.purchase_vendor_id.data = asset.purchase_vendor_id or 0
        form.service_vendor_id.data = asset.service_vendor_id or 0
        form.location_id.data = asset.location_id or 0
        form.assigned_user_id.data = asset.assigned_user_id or 0

    if form.validate_on_submit():
        _apply_asset_form(asset, form)
        db.session.commit()
        log_activity(f"Edited equipment {asset.asset_tag}")
        flash("Equipment asset updated.", "success")
        return redirect(
            url_for("equipment.view_equipment_asset", asset_id=asset.id)
        )

    return render_template(
        "equipment/asset_form_page.html",
        form=form,
        title=f"Edit {asset.asset_tag}",
        subtitle="Update the asset metadata, service schedule, who has it, and where it is located.",
    )


@equipment.route("/equipment/<int:asset_id>/archive", methods=["POST"])
@login_required
def archive_equipment_asset(asset_id: int):
    form = DeleteForm()
    if not form.validate_on_submit():
        abort(400)
    asset = _load_asset_or_404(asset_id)
    asset.archived = True
    db.session.commit()
    log_activity(f"Archived equipment {asset.asset_tag}")
    flash("Equipment asset archived.", "success")
    return redirect(url_for("equipment.view_equipment"))


@equipment.route("/equipment/intake")
@login_required
def view_equipment_intake():
    scope = request.endpoint or "equipment.view_equipment_intake"
    default_filters = get_filter_defaults(current_user, scope)
    active_filters = normalize_filters(
        request.args, exclude=("page", "per_page", "reset")
    )
    if request.args.get("reset"):
        return redirect(url_for("equipment.view_equipment_intake"))
    if default_filters and not active_filters:
        return redirect(
            url_for(
                "equipment.view_equipment_intake",
                **filters_to_query_args(default_filters),
            )
        )

    page = request.args.get("page", 1, type=int)
    per_page = get_per_page()
    search_query = normalize_request_text_filter(request.args.get("search_query"))
    match_mode = normalize_text_match_mode(request.args.get("match_mode"))
    source_type = (request.args.get("source_type") or "all").strip().lower()
    status = (request.args.get("status") or "all").strip().lower()
    category_id = request.args.get("category_id", type=int)
    model_id = request.args.get("model_id", type=int)
    purchase_vendor_id = request.args.get("purchase_vendor_id", type=int)
    location_id = request.args.get("location_id", type=int)

    valid_sources = {code for code, _label in EquipmentIntakeBatch.SOURCE_TYPE_CHOICES}
    valid_statuses = {code for code, _label in EquipmentIntakeBatch.STATUS_CHOICES}
    if source_type not in valid_sources | {"all"}:
        source_type = "all"
    if status not in valid_statuses | {"all"}:
        status = "all"

    query = (
        EquipmentIntakeBatch.query.options(
            selectinload(EquipmentIntakeBatch.equipment_model).selectinload(
                EquipmentModel.category
            ),
            selectinload(EquipmentIntakeBatch.purchase_vendor),
            selectinload(EquipmentIntakeBatch.purchase_order).selectinload(
                PurchaseOrder.vendor
            ),
            selectinload(EquipmentIntakeBatch.purchase_invoice),
            selectinload(EquipmentIntakeBatch.location),
            selectinload(EquipmentIntakeBatch.assigned_user),
            selectinload(EquipmentIntakeBatch.assets),
        )
        .join(
            EquipmentModel,
            EquipmentModel.id == EquipmentIntakeBatch.equipment_model_id,
        )
        .join(
            EquipmentCategory,
            EquipmentCategory.id == EquipmentModel.category_id,
        )
    )

    if search_query:
        query = query.filter(
            or_(
                build_text_match_predicate(
                    EquipmentModel.manufacturer, search_query, match_mode
                ),
                build_text_match_predicate(
                    EquipmentModel.name, search_query, match_mode
                ),
                build_text_match_predicate(
                    EquipmentModel.model_number, search_query, match_mode
                ),
                build_text_match_predicate(
                    EquipmentCategory.name, search_query, match_mode
                ),
                build_text_match_predicate(
                    EquipmentIntakeBatch.vendor_name, search_query, match_mode
                ),
                build_text_match_predicate(
                    EquipmentIntakeBatch.purchase_order_reference,
                    search_query,
                    match_mode,
                ),
                build_text_match_predicate(
                    EquipmentIntakeBatch.purchase_invoice_reference,
                    search_query,
                    match_mode,
                ),
            )
        )

    if source_type != "all":
        query = query.filter(EquipmentIntakeBatch.source_type == source_type)
    if status != "all":
        query = query.filter(EquipmentIntakeBatch.status == status)
    if category_id:
        query = query.filter(EquipmentCategory.id == category_id)
    if model_id:
        query = query.filter(EquipmentIntakeBatch.equipment_model_id == model_id)
    if purchase_vendor_id:
        query = query.filter(
            EquipmentIntakeBatch.purchase_vendor_id == purchase_vendor_id
        )
    if location_id:
        query = query.filter(EquipmentIntakeBatch.location_id == location_id)

    batches = query.order_by(
        EquipmentIntakeBatch.received_on.desc(),
        EquipmentIntakeBatch.order_date.desc(),
        EquipmentIntakeBatch.created_at.desc(),
    ).paginate(page=page, per_page=per_page)

    return render_template(
        "equipment/view_intake_batches.html",
        batches=batches,
        search_query=search_query,
        match_mode=match_mode,
        source_type=source_type,
        status=status,
        category_id=category_id,
        model_id=model_id,
        purchase_vendor_id=purchase_vendor_id,
        location_id=location_id,
        categories=EquipmentCategory.query.order_by(EquipmentCategory.name.asc()).all(),
        models=(
            EquipmentModel.query.options(selectinload(EquipmentModel.category))
            .order_by(
                EquipmentModel.manufacturer.asc(),
                EquipmentModel.name.asc(),
                EquipmentModel.model_number.asc(),
            )
            .all()
        ),
        vendors=Vendor.query.order_by(Vendor.first_name.asc(), Vendor.last_name.asc()).all(),
        locations=Location.query.order_by(Location.name.asc()).all(),
        source_type_choices=EquipmentIntakeBatch.SOURCE_TYPE_CHOICES,
        status_choices=EquipmentIntakeBatch.STATUS_CHOICES,
        selected_category=db.session.get(EquipmentCategory, category_id)
        if category_id
        else None,
        selected_model=db.session.get(EquipmentModel, model_id) if model_id else None,
        selected_vendor=db.session.get(Vendor, purchase_vendor_id)
        if purchase_vendor_id
        else None,
        selected_location=db.session.get(Location, location_id) if location_id else None,
        per_page=per_page,
        pagination_args=build_pagination_args(per_page),
    )


@equipment.route("/equipment/intake/create", methods=["GET", "POST"])
@login_required
def create_equipment_intake_batch():
    form = EquipmentIntakeBatchForm()
    if request.method == "GET":
        purchase_order_id = request.args.get("purchase_order_id", type=int)
        purchase_invoice_id = request.args.get("purchase_invoice_id", type=int)
        if purchase_invoice_id:
            purchase_invoice = db.session.get(PurchaseInvoice, purchase_invoice_id)
            if purchase_invoice is not None:
                purchase_order = purchase_invoice.purchase_order
                form.source_type.data = EquipmentIntakeBatch.SOURCE_PURCHASE_INVOICE
                form.purchase_invoice_id.data = purchase_invoice.id
                form.purchase_invoice_reference.data = (
                    purchase_invoice.invoice_number or ""
                )
                form.received_on.data = purchase_invoice.received_date
                form.location_id.data = purchase_invoice.location_id or 0
                if purchase_order is not None:
                    form.purchase_order_id.data = purchase_order.id
                    form.purchase_order_reference.data = (
                        purchase_order.order_number or ""
                    )
                    form.order_date.data = purchase_order.order_date
                    form.expected_received_on.data = purchase_order.expected_date
                    form.purchase_vendor_id.data = purchase_order.vendor_id or 0
                    form.vendor_name.data = purchase_order.vendor_name or ""
        elif purchase_order_id:
            purchase_order = db.session.get(PurchaseOrder, purchase_order_id)
            if purchase_order is not None:
                form.source_type.data = EquipmentIntakeBatch.SOURCE_PURCHASE_ORDER
                form.purchase_order_id.data = purchase_order.id
                form.purchase_order_reference.data = purchase_order.order_number or ""
                form.order_date.data = purchase_order.order_date
                form.expected_received_on.data = purchase_order.expected_date
                form.purchase_vendor_id.data = purchase_order.vendor_id or 0
                form.vendor_name.data = purchase_order.vendor_name or ""

    if form.validate_on_submit():
        batch = EquipmentIntakeBatch(created_by_id=current_user.id)
        _apply_intake_batch_form(batch, form)
        db.session.add(batch)
        db.session.commit()
        log_activity(
            f"Created equipment intake batch #{batch.id} for {batch.model_display_name}"
        )
        flash("Equipment intake batch created.", "success")
        return redirect(
            url_for("equipment.view_equipment_intake_batch", batch_id=batch.id)
        )

    return render_template(
        "equipment/intake_batch_form_page.html",
        form=form,
        title="Create Equipment Intake Batch",
        subtitle="Track a linked purchase, expected quantity, and where the received assets will be staged.",
    )


@equipment.route("/equipment/intake/<int:batch_id>")
@login_required
def view_equipment_intake_batch(batch_id: int):
    batch = _load_intake_batch_or_404(batch_id)
    return render_template(
        "equipment/view_intake_batch.html",
        batch=batch,
    )


@equipment.route("/equipment/intake/<int:batch_id>/edit", methods=["GET", "POST"])
@login_required
def edit_equipment_intake_batch(batch_id: int):
    batch = _load_intake_batch_or_404(batch_id)
    form = EquipmentIntakeBatchForm(obj=batch)

    if request.method == "GET":
        form.purchase_vendor_id.data = batch.purchase_vendor_id or 0
        form.purchase_order_id.data = batch.purchase_order_id or 0
        form.purchase_invoice_id.data = batch.purchase_invoice_id or 0
        form.location_id.data = batch.location_id or 0
        form.assigned_user_id.data = batch.assigned_user_id or 0

    if form.validate_on_submit():
        _apply_intake_batch_form(batch, form)
        db.session.commit()
        log_activity(f"Edited equipment intake batch #{batch.id}")
        flash("Equipment intake batch updated.", "success")
        return redirect(
            url_for("equipment.view_equipment_intake_batch", batch_id=batch.id)
        )

    return render_template(
        "equipment/intake_batch_form_page.html",
        form=form,
        title=f"Edit Intake Batch #{batch.id}",
        subtitle="Adjust the purchasing references, expected quantity, and default receiving details.",
    )


@equipment.route("/equipment/intake/<int:batch_id>/receive", methods=["GET", "POST"])
@login_required
def receive_equipment_intake_batch(batch_id: int):
    batch = _load_intake_batch_or_404(batch_id)
    if batch.status == EquipmentIntakeBatch.STATUS_CANCELLED:
        flash("Cancelled intake batches cannot receive assets.", "danger")
        return redirect(url_for("equipment.view_equipment_intake_batch", batch_id=batch.id))
    if batch.remaining_quantity <= 0:
        flash(
            "This intake batch has no remaining quantity. Increase the planned quantity before receiving more assets.",
            "info",
        )
        return redirect(url_for("equipment.view_equipment_intake_batch", batch_id=batch.id))

    form = EquipmentIntakeReceiveForm(batch=batch)
    if request.method == "GET":
        remaining_quantity = batch.remaining_quantity or batch.expected_quantity or 1
        form.quantity.data = max(remaining_quantity, 1)
        form.location_id.data = batch.location_id or 0
        form.assigned_user_id.data = batch.assigned_user_id or 0
        form.acquired_on.data = batch.received_on
        if batch.unit_cost is not None:
            form.cost.data = batch.unit_cost

    if form.validate_on_submit():
        materialized_rows = _materialize_received_asset_rows(form)
        acquired_on = form.acquired_on.data or batch.received_on or date_cls.today()
        location_id = form.location_id.data or batch.location_id
        assigned_user_id = form.assigned_user_id.data or batch.assigned_user_id
        cost_value = (
            float(form.cost.data)
            if form.cost.data is not None
            else batch.unit_cost
        )

        for row in materialized_rows:
            db.session.add(
                EquipmentAsset(
                    equipment_model_id=batch.equipment_model_id,
                    intake_batch=batch,
                    name=row["name"],
                    asset_tag=row["asset_tag"],
                    serial_number=row["serial_number"],
                    status=form.status.data,
                    acquired_on=acquired_on,
                    warranty_expires_on=form.warranty_expires_on.data,
                    cost=cost_value,
                    purchase_vendor_id=batch.purchase_vendor_id,
                    location_id=location_id,
                    sublocation=row["sublocation"] or form.sublocation.data or None,
                    assigned_user_id=assigned_user_id,
                )
            )

        if batch.location_id is None and location_id:
            batch.location_id = location_id
        if batch.assigned_user_id is None and assigned_user_id:
            batch.assigned_user_id = assigned_user_id
        batch.received_on = batch.received_on or acquired_on
        batch.sync_status()

        db.session.commit()
        log_activity(
            f"Received {len(materialized_rows)} equipment asset(s) into intake batch #{batch.id}"
        )
        flash(
            f"Received {len(materialized_rows)} equipment asset(s) into this batch.",
            "success",
        )
        return redirect(
            url_for("equipment.view_equipment_intake_batch", batch_id=batch.id)
        )

    return render_template(
        "equipment/receive_intake_batch_page.html",
        form=form,
        batch=batch,
    )


@equipment.route("/equipment/import/snipe-it", methods=["GET", "POST"])
@login_required
def import_equipment_from_snipe_it():
    form = EquipmentSnipeItImportForm()
    summary = None
    if form.validate_on_submit():
        file_storage = form.file.data
        try:
            summary = run_snipe_it_import(
                file_storage.stream,
                default_category_name=form.default_category_name.data,
                create_missing_locations=bool(form.create_missing_locations.data),
                update_existing=bool(form.update_existing.data),
                imported_by_id=current_user.id,
                source_filename=getattr(file_storage, "filename", None),
            )
            db.session.commit()
        except EquipmentImportError as exc:
            db.session.rollback()
            form.file.errors.append(str(exc))
        except Exception:
            db.session.rollback()
            current_app.logger.exception("Failed to import Snipe-IT equipment CSV")
            form.file.errors.append(
                "The equipment import failed unexpectedly. Check the file and try again."
            )
        else:
            log_activity(
                "Imported equipment from Snipe-IT CSV "
                f"(created {summary['created_count']}, updated {summary['updated_count']}, skipped {summary['skipped_count']})"
            )
            flash(
                "Snipe-IT import completed: "
                f"{summary['created_count']} created, "
                f"{summary['updated_count']} updated, "
                f"{summary['skipped_count']} skipped.",
                "success",
            )

    return render_template(
        "equipment/import_snipe_it.html",
        form=form,
        summary=summary,
    )


@equipment.route("/equipment/maintenance")
@login_required
def view_equipment_maintenance():
    scope = request.endpoint or "equipment.view_equipment_maintenance"
    default_filters = get_filter_defaults(current_user, scope)
    active_filters = normalize_filters(
        request.args, exclude=("page", "per_page", "reset")
    )
    if request.args.get("reset"):
        return redirect(url_for("equipment.view_equipment_maintenance"))
    if default_filters and not active_filters:
        return redirect(
            url_for(
                "equipment.view_equipment_maintenance",
                **filters_to_query_args(default_filters),
            )
        )

    today = date_cls.today()
    soon_cutoff = today + timedelta(days=EquipmentMaintenanceIssue.DUE_SOON_DAYS)
    page = request.args.get("page", 1, type=int)
    per_page = get_per_page()
    search_query = normalize_request_text_filter(
        request.args.get("search_query")
    )
    match_mode = normalize_text_match_mode(request.args.get("match_mode"))
    status = (request.args.get("status") or "open").strip().lower()
    priority = (request.args.get("priority") or "all").strip().lower()
    due_state = (request.args.get("due_state") or "all").strip().lower()
    asset_id = request.args.get("asset_id", type=int)
    assigned_user_id = request.args.get("assigned_user_id", type=int)
    assigned_vendor_id = request.args.get("assigned_vendor_id", type=int)

    valid_statuses = {code for code, _label in EquipmentMaintenanceIssue.STATUS_CHOICES}
    valid_priorities = {
        code for code, _label in EquipmentMaintenanceIssue.PRIORITY_CHOICES
    }
    if status not in valid_statuses | {"all", "open"}:
        status = "open"
    if priority not in valid_priorities | {"all"}:
        priority = "all"
    if due_state not in {"all", "overdue", "due_soon", "no_due_date", "closed"}:
        due_state = "all"

    query = (
        EquipmentMaintenanceIssue.query.options(
            selectinload(EquipmentMaintenanceIssue.equipment_asset).selectinload(
                EquipmentAsset.equipment_model
            ),
            selectinload(EquipmentMaintenanceIssue.assigned_user),
            selectinload(EquipmentMaintenanceIssue.assigned_vendor),
        )
        .join(
            EquipmentAsset,
            EquipmentAsset.id == EquipmentMaintenanceIssue.equipment_asset_id,
        )
    )

    if search_query:
        query = query.filter(
            or_(
                build_text_match_predicate(
                    EquipmentMaintenanceIssue.title, search_query, match_mode
                ),
                build_text_match_predicate(
                    EquipmentMaintenanceIssue.description, search_query, match_mode
                ),
                build_text_match_predicate(
                    EquipmentMaintenanceIssue.resolution_summary,
                    search_query,
                    match_mode,
                ),
                build_text_match_predicate(
                    EquipmentAsset.asset_tag, search_query, match_mode
                ),
                build_text_match_predicate(
                    EquipmentAsset.name, search_query, match_mode
                ),
            )
        )

    if status == "open":
        query = query.filter(
            EquipmentMaintenanceIssue.status.in_(
                tuple(EquipmentMaintenanceIssue.OPEN_STATUSES)
            )
        )
    elif status != "all":
        query = query.filter(EquipmentMaintenanceIssue.status == status)

    if priority != "all":
        query = query.filter(EquipmentMaintenanceIssue.priority == priority)
    if asset_id:
        query = query.filter(EquipmentMaintenanceIssue.equipment_asset_id == asset_id)
    if assigned_user_id:
        query = query.filter(
            EquipmentMaintenanceIssue.assigned_user_id == assigned_user_id
        )
    if assigned_vendor_id:
        query = query.filter(
            EquipmentMaintenanceIssue.assigned_vendor_id == assigned_vendor_id
        )
    if due_state == "overdue":
        query = query.filter(
            EquipmentMaintenanceIssue.due_on.is_not(None),
            EquipmentMaintenanceIssue.due_on < today,
            EquipmentMaintenanceIssue.status.in_(
                tuple(EquipmentMaintenanceIssue.OPEN_STATUSES)
            ),
        )
    elif due_state == "due_soon":
        query = query.filter(
            EquipmentMaintenanceIssue.due_on.is_not(None),
            EquipmentMaintenanceIssue.due_on >= today,
            EquipmentMaintenanceIssue.due_on <= soon_cutoff,
            EquipmentMaintenanceIssue.status.in_(
                tuple(EquipmentMaintenanceIssue.OPEN_STATUSES)
            ),
        )
    elif due_state == "no_due_date":
        query = query.filter(EquipmentMaintenanceIssue.due_on.is_(None))
    elif due_state == "closed":
        query = query.filter(
            EquipmentMaintenanceIssue.status.in_(
                (
                    EquipmentMaintenanceIssue.STATUS_RESOLVED,
                    EquipmentMaintenanceIssue.STATUS_CANCELLED,
                )
            )
        )

    status_order = case(
        (
            EquipmentMaintenanceIssue.status == EquipmentMaintenanceIssue.STATUS_OPEN,
            0,
        ),
        (
            EquipmentMaintenanceIssue.status
            == EquipmentMaintenanceIssue.STATUS_IN_PROGRESS,
            1,
        ),
        (
            EquipmentMaintenanceIssue.status
            == EquipmentMaintenanceIssue.STATUS_WAITING_VENDOR,
            2,
        ),
        (
            EquipmentMaintenanceIssue.status
            == EquipmentMaintenanceIssue.STATUS_RESOLVED,
            3,
        ),
        else_=4,
    )
    priority_order = case(
        (
            EquipmentMaintenanceIssue.priority
            == EquipmentMaintenanceIssue.PRIORITY_CRITICAL,
            0,
        ),
        (
            EquipmentMaintenanceIssue.priority
            == EquipmentMaintenanceIssue.PRIORITY_HIGH,
            1,
        ),
        (
            EquipmentMaintenanceIssue.priority
            == EquipmentMaintenanceIssue.PRIORITY_MEDIUM,
            2,
        ),
        else_=3,
    )

    issues = query.order_by(
        status_order.asc(),
        priority_order.asc(),
        EquipmentMaintenanceIssue.due_on.asc(),
        EquipmentMaintenanceIssue.created_at.desc(),
    ).paginate(page=page, per_page=per_page)

    return render_template(
        "equipment/view_maintenance.html",
        issues=issues,
        search_query=search_query,
        match_mode=match_mode,
        status=status,
        priority=priority,
        due_state=due_state,
        asset_id=asset_id,
        assigned_user_id=assigned_user_id,
        assigned_vendor_id=assigned_vendor_id,
        assets=EquipmentAsset.query.order_by(EquipmentAsset.asset_tag.asc()).all(),
        users=sorted(
            User.query.filter_by(active=True).all(),
            key=lambda user: (user.sort_key, user.email.casefold()),
        ),
        vendors=Vendor.query.order_by(Vendor.first_name.asc(), Vendor.last_name.asc()).all(),
        status_choices=EquipmentMaintenanceIssue.STATUS_CHOICES,
        priority_choices=EquipmentMaintenanceIssue.PRIORITY_CHOICES,
        selected_asset=db.session.get(EquipmentAsset, asset_id) if asset_id else None,
        selected_user=db.session.get(User, assigned_user_id)
        if assigned_user_id
        else None,
        selected_vendor=db.session.get(Vendor, assigned_vendor_id)
        if assigned_vendor_id
        else None,
        per_page=per_page,
        pagination_args=build_pagination_args(per_page),
    )


@equipment.route("/equipment/maintenance/create", methods=["GET", "POST"])
@login_required
def create_equipment_maintenance_issue():
    form = EquipmentMaintenanceIssueForm()
    asset_id = request.args.get("asset_id", type=int)
    if request.method == "GET" and asset_id and db.session.get(EquipmentAsset, asset_id):
        form.equipment_asset_id.data = asset_id
    if form.validate_on_submit():
        issue = EquipmentMaintenanceIssue(created_by_id=current_user.id)
        _apply_issue_form(issue, form)
        db.session.add(issue)
        db.session.flush()
        _record_issue_update(
            issue,
            event_type=EquipmentMaintenanceUpdate.EVENT_CREATED,
            new_status=issue.status,
        )
        db.session.commit()
        log_activity(
            f"Created maintenance issue #{issue.id} for equipment {issue.equipment_asset.asset_tag}"
        )
        flash("Maintenance issue created.", "success")
        return redirect(
            url_for(
                "equipment.view_equipment_maintenance_issue", issue_id=issue.id
            )
        )
    return render_template(
        "equipment/maintenance_issue_form_page.html",
        form=form,
        title="Report Maintenance Issue",
        subtitle="Track service work, downtime, responsibility, and cost for a specific asset.",
    )


@equipment.route("/equipment/maintenance/<int:issue_id>")
@login_required
def view_equipment_maintenance_issue(issue_id: int):
    issue = _load_issue_or_404(issue_id)
    update_form = EquipmentMaintenanceUpdateForm()
    return render_template(
        "equipment/view_maintenance_issue.html",
        issue=issue,
        update_form=update_form,
    )


@equipment.route("/equipment/maintenance/<int:issue_id>/edit", methods=["GET", "POST"])
@login_required
def edit_equipment_maintenance_issue(issue_id: int):
    issue = _load_issue_or_404(issue_id)
    form = EquipmentMaintenanceIssueForm(obj=issue)

    if request.method == "GET":
        form.equipment_asset_id.data = issue.equipment_asset_id
        form.assigned_user_id.data = issue.assigned_user_id or 0
        form.assigned_vendor_id.data = issue.assigned_vendor_id or 0

    if form.validate_on_submit():
        previous_status, reopened = _apply_issue_form(issue, form)
        event_type = EquipmentMaintenanceUpdate.EVENT_EDITED
        message = "Issue details updated."
        if previous_status != issue.status:
            event_type = EquipmentMaintenanceUpdate.EVENT_STATUS_CHANGED
            if reopened:
                message = "Issue reopened."
            elif issue.status == EquipmentMaintenanceIssue.STATUS_RESOLVED:
                message = "Issue resolved."
            elif issue.status == EquipmentMaintenanceIssue.STATUS_CANCELLED:
                message = "Issue cancelled."
            else:
                message = f"Issue moved to {issue.status_label.lower()}."
        _record_issue_update(
            issue,
            event_type=event_type,
            message=message,
            previous_status=previous_status,
            new_status=issue.status,
        )
        db.session.commit()
        log_activity(
            f"Edited maintenance issue #{issue.id} for equipment {issue.equipment_asset.asset_tag}"
        )
        flash("Maintenance issue updated.", "success")
        return redirect(
            url_for(
                "equipment.view_equipment_maintenance_issue", issue_id=issue.id
            )
        )

    return render_template(
        "equipment/maintenance_issue_form_page.html",
        form=form,
        title=f"Edit Issue #{issue.id}",
        subtitle=f"{issue.equipment_asset.asset_tag} - {issue.title}",
    )


@equipment.route("/equipment/maintenance/<int:issue_id>/updates", methods=["POST"])
@login_required
def add_equipment_maintenance_update(issue_id: int):
    issue = _load_issue_or_404(issue_id)
    form = EquipmentMaintenanceUpdateForm()
    if not form.validate_on_submit():
        return (
            render_template(
                "equipment/view_maintenance_issue.html",
                issue=issue,
                update_form=form,
            ),
            400,
        )

    previous_status = issue.status
    if form.status.data:
        _apply_issue_status(issue, form.status.data)
    issue.updated_at = datetime.utcnow()

    event_type = EquipmentMaintenanceUpdate.EVENT_COMMENT
    if previous_status != issue.status:
        event_type = EquipmentMaintenanceUpdate.EVENT_STATUS_CHANGED

    _record_issue_update(
        issue,
        event_type=event_type,
        message=form.message.data,
        previous_status=previous_status,
        new_status=issue.status,
    )
    db.session.commit()

    if previous_status != issue.status:
        log_activity(
            f"Updated maintenance issue #{issue.id} for equipment {issue.equipment_asset.asset_tag} to {issue.status_label.lower()}"
        )
    else:
        log_activity(
            f"Added maintenance update to issue #{issue.id} for equipment {issue.equipment_asset.asset_tag}"
        )
    flash("Maintenance update saved.", "success")
    return redirect(
        url_for("equipment.view_equipment_maintenance_issue", issue_id=issue.id)
    )


@equipment.route("/equipment/catalog")
@login_required
def view_equipment_catalog():
    categories = (
        EquipmentCategory.query.options(selectinload(EquipmentCategory.models))
        .order_by(EquipmentCategory.name.asc())
        .all()
    )
    models = (
        EquipmentModel.query.options(
            selectinload(EquipmentModel.category),
            selectinload(EquipmentModel.assets),
        )
        .order_by(
            EquipmentModel.manufacturer.asc(),
            EquipmentModel.name.asc(),
            EquipmentModel.model_number.asc(),
        )
        .all()
    )
    delete_form = DeleteForm()
    return render_template(
        "equipment/catalog.html",
        categories=categories,
        models=models,
        delete_form=delete_form,
    )


@equipment.route("/equipment/categories/create", methods=["GET", "POST"])
@login_required
def create_equipment_category():
    form = EquipmentCategoryForm()
    if form.validate_on_submit():
        category = EquipmentCategory(
            name=form.name.data,
            description=(form.description.data or "").strip() or None,
        )
        db.session.add(category)
        db.session.commit()
        log_activity(f"Created equipment category {category.name}")
        flash("Equipment category created.", "success")
        return redirect(url_for("equipment.view_equipment_catalog"))
    return render_template(
        "equipment/category_form_page.html",
        form=form,
        title="Add Equipment Category",
    )


@equipment.route("/equipment/categories/<int:category_id>/edit", methods=["GET", "POST"])
@login_required
def edit_equipment_category(category_id: int):
    category = db.session.get(EquipmentCategory, category_id)
    if category is None:
        abort(404)
    form = EquipmentCategoryForm(obj=category, obj_id=category.id)
    if form.validate_on_submit():
        category.name = form.name.data
        category.description = (form.description.data or "").strip() or None
        db.session.commit()
        log_activity(f"Edited equipment category {category.name}")
        flash("Equipment category updated.", "success")
        return redirect(url_for("equipment.view_equipment_catalog"))
    return render_template(
        "equipment/category_form_page.html",
        form=form,
        title=f"Edit {category.name}",
    )


@equipment.route("/equipment/categories/<int:category_id>/archive", methods=["POST"])
@login_required
def archive_equipment_category(category_id: int):
    form = DeleteForm()
    if not form.validate_on_submit():
        abort(400)
    category = db.session.get(EquipmentCategory, category_id)
    if category is None:
        abort(404)
    category.archived = True
    db.session.commit()
    log_activity(f"Archived equipment category {category.name}")
    flash("Equipment category archived.", "success")
    return redirect(url_for("equipment.view_equipment_catalog"))


@equipment.route("/equipment/models/create", methods=["GET", "POST"])
@login_required
def create_equipment_model():
    form = EquipmentModelForm()
    if form.validate_on_submit():
        equipment_model = EquipmentModel(
            category_id=form.category_id.data,
            manufacturer=form.manufacturer.data,
            name=form.name.data,
            model_number=form.model_number.data,
            description=(form.description.data or "").strip() or None,
        )
        db.session.add(equipment_model)
        db.session.commit()
        log_activity(f"Created equipment model {equipment_model.display_name}")
        flash("Equipment model created.", "success")
        return redirect(url_for("equipment.view_equipment_catalog"))
    return render_template(
        "equipment/model_form_page.html",
        form=form,
        title="Add Equipment Model",
    )


@equipment.route("/equipment/models/<int:model_id>/edit", methods=["GET", "POST"])
@login_required
def edit_equipment_model(model_id: int):
    equipment_model = db.session.get(EquipmentModel, model_id)
    if equipment_model is None:
        abort(404)
    form = EquipmentModelForm(obj=equipment_model, obj_id=equipment_model.id)
    if form.validate_on_submit():
        equipment_model.category_id = form.category_id.data
        equipment_model.manufacturer = form.manufacturer.data
        equipment_model.name = form.name.data
        equipment_model.model_number = form.model_number.data
        equipment_model.description = (form.description.data or "").strip() or None
        db.session.commit()
        log_activity(f"Edited equipment model {equipment_model.display_name}")
        flash("Equipment model updated.", "success")
        return redirect(url_for("equipment.view_equipment_catalog"))
    return render_template(
        "equipment/model_form_page.html",
        form=form,
        title=f"Edit {equipment_model.display_name}",
    )


@equipment.route("/equipment/models/<int:model_id>/archive", methods=["POST"])
@login_required
def archive_equipment_model(model_id: int):
    form = DeleteForm()
    if not form.validate_on_submit():
        abort(400)
    equipment_model = db.session.get(EquipmentModel, model_id)
    if equipment_model is None:
        abort(404)
    equipment_model.archived = True
    db.session.commit()
    log_activity(f"Archived equipment model {equipment_model.display_name}")
    flash("Equipment model archived.", "success")
    return redirect(url_for("equipment.view_equipment_catalog"))


@equipment.route("/equipment/labels/print")
@login_required
def print_equipment_labels():
    raw_ids = request.args.getlist("equipment_id")
    ordered_ids = []
    for raw_value in raw_ids:
        try:
            asset_id = int(raw_value)
        except (TypeError, ValueError):
            continue
        if asset_id not in ordered_ids:
            ordered_ids.append(asset_id)
    if not ordered_ids:
        abort(400)

    assets = _ordered_equipment_assets(ordered_ids)
    qr_payloads = {
        asset.id: url_for(
            "equipment.view_equipment_asset",
            asset_id=asset.id,
            _external=True,
        )
        for asset in assets
    }

    try:
        pdf_bytes = render_equipment_label_pdf(assets, qr_payloads)
    except Exception:
        current_app.logger.exception(
            "Failed to render equipment labels for assets %s",
            ", ".join(map(str, ordered_ids)),
        )
        abort(500)

    log_activity(
        "Printed equipment label(s) for assets %s"
        % ", ".join(str(asset.id) for asset in assets)
    )
    filename = (
        "equipment-labels.pdf"
        if len(assets) > 1
        else f"{assets[0].asset_tag}-label.pdf"
    )
    response = Response(pdf_bytes, mimetype="application/pdf")
    response.headers["Content-Disposition"] = f"inline; filename={filename}"
    return response
