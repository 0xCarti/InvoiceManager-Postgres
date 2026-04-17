from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta
from types import SimpleNamespace

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy.orm import selectinload

from app import db
from app.forms import (
    AvailabilityOverrideForm,
    AvailabilityWindowForm,
    CSRFOnlyForm,
    DepartmentForm,
    ShiftForm,
    ShiftPositionForm,
    TimeOffRequestForm,
    TimeOffReviewForm,
    TradeboardClaimReviewForm,
    UserDepartmentMembershipForm,
    UserPositionEligibilityForm,
    UserScheduleProfileForm,
)
from app.models import (
    AvailabilityOverride,
    Department,
    DepartmentScheduleWeek,
    Event,
    EventLocation,
    RecurringAvailabilityWindow,
    Shift,
    ShiftPosition,
    TimeOffRequest,
    TradeboardClaim,
    User,
    UserDepartmentMembership,
    UserPositionEligibility,
)
from app.services.schedule_service import (
    apply_rate_snapshot,
    auto_assign_shifts,
    capture_shift_snapshot,
    find_overlapping_shift,
    format_week_label,
    get_or_create_schedule_week,
    get_visible_departments,
    get_visible_schedule_users,
    log_schedule_action,
    mark_schedule_week_seen,
    material_change_fields,
    normalize_week_start,
    notify_schedule_changes,
    notify_schedule_posted,
    notify_time_off_reviewed,
    notify_time_off_submitted,
    override_blocks_shift,
    record_shift_audit,
    time_off_overlaps,
    user_can_auto_assign_department,
    user_can_manage_department,
    user_can_manage_other_user,
    user_department_ids,
    user_is_schedule_gm,
)


schedule = Blueprint("schedule", __name__)
ALL_DEPARTMENTS_VALUE = "all"
SCHEDULE_VIEW_USER = "user"
SCHEDULE_VIEW_POSITION = "position"


def _parse_int(value, default=None):
    try:
        if value in (None, "", 0, "0"):
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


def _parse_checkbox(value) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "y", "yes", "on"}


def _schedule_redirect(
    endpoint: str,
    department_id: int | None,
    week_start,
    *,
    user_id: int | None = None,
    view_mode: str | None = None,
    filter_event_id: int | None = None,
    filter_location_id: int | None = None,
) -> str:
    values = {"week_start": normalize_week_start(week_start).isoformat()}
    if department_id:
        values["department_id"] = department_id
    if user_id:
        values["user_id"] = user_id
    if view_mode == SCHEDULE_VIEW_POSITION:
        values["view_mode"] = view_mode
    if filter_event_id:
        values["filter_event_id"] = filter_event_id
    if filter_location_id:
        values["filter_location_id"] = filter_location_id
    return url_for(endpoint, **values)


def _select_department(
    departments: list[Department], requested_department_id: int | None
):
    if not departments:
        return None
    if requested_department_id:
        for department in departments:
            if department.id == requested_department_id:
                return department
    return departments[0]


def _parse_department_filter_value(value) -> str | int | None:
    if isinstance(value, str) and value.strip().lower() == ALL_DEPARTMENTS_VALUE:
        return ALL_DEPARTMENTS_VALUE
    return _parse_int(value)


def _manager_scope_users(actor: User) -> list[User]:
    if getattr(actor, "is_super_admin", False) or user_is_schedule_gm(actor):
        users = User.query.filter(User.active.is_(True)).all()
        return sorted(users, key=lambda user: (user.sort_key, user.email.casefold()))
    department_ids = sorted(user_department_ids(actor))
    if not department_ids:
        return []
    memberships = (
        UserDepartmentMembership.query.options(
            selectinload(UserDepartmentMembership.user),
        )
        .filter(UserDepartmentMembership.department_id.in_(department_ids))
        .all()
    )
    scoped: dict[int, User] = {}
    for membership in memberships:
        user = membership.user
        if user is None or not user.active:
            continue
        if user_can_manage_other_user(actor, user, membership.department_id):
            scoped[user.id] = user
    return sorted(scoped.values(), key=lambda user: (user.sort_key, user.email.casefold()))


def _filter_schedule_users(
    users: list[User], requested_user_id: int | None
) -> tuple[list[User], int | None]:
    if requested_user_id is None:
        return users, None
    for user in users:
        if user.id == requested_user_id:
            return [user], requested_user_id
    return users, None


def _build_team_schedule_filter_users(
    departments: list[Department],
    *,
    include_self_only: bool,
) -> list[User]:
    if include_self_only:
        return [current_user]
    scoped: dict[int, User] = {}
    for department in departments:
        for user in get_visible_schedule_users(
            current_user,
            department.id,
            include_self_only=False,
        ):
            scoped[user.id] = user
    return sorted(scoped.values(), key=lambda user: (user.sort_key, user.email.casefold()))


def _auto_assignable_departments(
    actor: User,
    departments: list[Department],
) -> list[Department]:
    return [
        department
        for department in departments
        if user_can_auto_assign_department(actor, department.id)
    ]


def _auto_assign_result_summary(results) -> tuple[int, int, str]:
    assigned_count = sum(1 for result in results if result.assigned_user_id)
    unassigned_count = len(results) - assigned_count
    unassigned_reasons = sorted(
        {
            result.summary
            for result in results
            if not result.assigned_user_id and result.summary
        }
    )
    reason_note = ""
    if unassigned_reasons:
        reason_note = f" {'; '.join(unassigned_reasons[:2])}"
    return assigned_count, unassigned_count, reason_note


def _current_seen_count_for_users(
    receipts_by_user_id: dict[int, object],
    current_version: int,
    users: list[User],
) -> int:
    return sum(
        1
        for user in users
        if (
            receipts_by_user_id.get(user.id) is not None
            and (receipts_by_user_id[user.id].last_seen_version or 0) >= current_version
        )
    )


def _can_manage_user_in_any_department(actor: User, target_user: User) -> bool:
    if getattr(actor, "is_super_admin", False) or user_is_schedule_gm(actor):
        return True
    for membership in target_user.department_memberships:
        if user_can_manage_other_user(actor, target_user, membership.department_id):
            return True
    return False


def _team_schedule_access_mode() -> tuple[bool, bool]:
    can_team = current_user.has_any_permission(
        "schedules.view_team",
        "schedules.edit_team",
        "schedules.publish",
        "schedules.view_labor",
        "schedules.view_seen_status",
        "schedules.auto_assign",
        "schedules.delete",
    )
    can_self = current_user.has_permission("schedules.self_schedule")
    return can_team, can_self


def _parse_schedule_view_mode(value) -> str:
    normalized = str(value or "").strip().lower()
    if normalized == SCHEDULE_VIEW_POSITION:
        return SCHEDULE_VIEW_POSITION
    return SCHEDULE_VIEW_USER


def _load_schedule_events_for_week(week_start) -> list[Event]:
    normalized_week_start = normalize_week_start(week_start)
    week_end = normalized_week_start + timedelta(days=6)
    return (
        Event.query.options(
            selectinload(Event.locations).selectinload(EventLocation.location),
        )
        .filter(
            Event.start_date <= week_end,
            Event.end_date >= normalized_week_start,
        )
        .order_by(Event.start_date.asc(), Event.name.asc())
        .all()
    )


def _select_schedule_event(events: list[Event], event_id: int | None) -> Event | None:
    if not event_id:
        return None
    for event in events:
        if event.id == event_id:
            return event
    return None


def _sorted_event_locations(event: Event | None) -> list[EventLocation]:
    if event is None:
        return []
    return sorted(
        [
            event_location
            for event_location in event.locations
            if event_location.location is not None
        ],
        key=lambda item: item.location.name.casefold(),
    )


def _build_schedule_event_location_map(events: list[Event]) -> dict[str, list[dict[str, str | int]]]:
    mapping: dict[str, list[dict[str, str | int]]] = {"0": []}
    for event in events:
        mapping[str(event.id)] = [
            {
                "id": event_location.location_id,
                "name": event_location.location.name,
            }
            for event_location in _sorted_event_locations(event)
        ]
    return mapping


def _filter_shifts_for_position_view(
    shifts: list[Shift],
    *,
    week_dates: list,
    visible_user_ids: set[int],
    selected_event_id: int | None,
    selected_location_id: int | None,
) -> list[Shift]:
    week_date_set = set(week_dates)
    filtered: list[Shift] = []
    for shift in shifts:
        if shift.shift_date not in week_date_set:
            continue
        if shift.assigned_user_id and shift.assigned_user_id not in visible_user_ids:
            continue
        if selected_event_id and shift.event_id != selected_event_id:
            continue
        if selected_location_id and shift.location_id != selected_location_id:
            continue
        filtered.append(shift)
    return sorted(
        filtered,
        key=lambda value: (
            value.shift_date,
            getattr(value.position.department, "name", "") if value.position else "",
            getattr(value.position, "sort_order", 0) if value.position else 0,
            getattr(value.position, "name", "") if value.position else "",
            value.start_time,
            getattr(value.assigned_user, "sort_key", "")
            if value.assigned_user is not None
            else "",
            value.id,
        ),
    )


def _build_position_schedule_days(
    shifts: list[Shift],
    *,
    week_dates: list,
    show_department_names: bool,
) -> list[dict]:
    shifts_by_day: dict[object, list[Shift]] = defaultdict(list)
    for shift in shifts:
        shifts_by_day[shift.shift_date].append(shift)

    day_rows: list[dict] = []
    for day in week_dates:
        position_groups: dict[tuple[int, int], dict] = {}
        day_shifts = shifts_by_day.get(day, [])
        for shift in day_shifts:
            position = shift.position
            if position is None:
                continue
            department = position.department
            group_key = (
                department.id if show_department_names and department is not None else 0,
                position.id,
            )
            group = position_groups.setdefault(
                group_key,
                {
                    "position": position,
                    "department": department,
                    "shifts": [],
                    "shift_count": 0,
                    "total_hours": 0.0,
                },
            )
            group["shifts"].append(shift)
            group["shift_count"] += 1
            group["total_hours"] += float(shift.paid_hours or 0.0)

        ordered_groups = sorted(
            position_groups.values(),
            key=lambda item: (
                item["department"].name.casefold()
                if show_department_names and item["department"] is not None
                else "",
                int(getattr(item["position"], "sort_order", 0) or 0),
                item["position"].name.casefold(),
            ),
        )
        for group in ordered_groups:
            group["shifts"].sort(
                key=lambda value: (
                    value.start_time,
                    getattr(value.assigned_user, "sort_key", "")
                    if value.assigned_user is not None
                    else "",
                    value.id,
                )
            )

        if show_department_names:
            sections_map: dict[int, dict] = {}
            for group in ordered_groups:
                department = group["department"]
                if department is None:
                    continue
                section = sections_map.setdefault(
                    department.id,
                    {
                        "department": department,
                        "position_groups": [],
                    },
                )
                section["position_groups"].append(group)
            sections = list(sections_map.values())
        else:
            sections = []
            if ordered_groups:
                sections.append({"department": None, "position_groups": ordered_groups})

        day_rows.append(
            {
                "date": day,
                "shift_count": len(day_shifts),
                "total_hours": round(
                    sum(float(shift.paid_hours or 0.0) for shift in day_shifts),
                    2,
                ),
                "sections": sections,
            }
        )
    return day_rows


def _prepare_schedule_week_context(
    department: Department,
    week_start,
    *,
    include_self_only: bool,
):
    schedule_week = (
        DepartmentScheduleWeek.query.options(
            selectinload(DepartmentScheduleWeek.shifts)
            .selectinload(Shift.position)
            .selectinload(ShiftPosition.department),
            selectinload(DepartmentScheduleWeek.shifts).selectinload(
                Shift.assigned_user
            ),
            selectinload(DepartmentScheduleWeek.receipts),
        )
        .filter_by(
            department_id=department.id,
            week_start=normalize_week_start(week_start),
        )
        .first()
    )
    if schedule_week is None:
        schedule_week = get_or_create_schedule_week(
            department.id, normalize_week_start(week_start)
        )
        db.session.flush()

    visible_users = get_visible_schedule_users(
        current_user,
        department.id,
        include_self_only=include_self_only,
    )
    week_dates = [
        schedule_week.week_start + timedelta(days=offset) for offset in range(7)
    ]
    shifts_by_row: dict[tuple[int, object], list[Shift]] = defaultdict(list)
    open_shifts: list[Shift] = []
    for shift in sorted(
        schedule_week.shifts,
        key=lambda value: (value.shift_date, value.start_time, value.id),
    ):
        if shift.assignment_mode != Shift.ASSIGNMENT_ASSIGNED or not shift.assigned_user_id:
            open_shifts.append(shift)
            shifts_by_row[(-1, shift.shift_date)].append(shift)
            continue
        shifts_by_row[(shift.assigned_user_id, shift.shift_date)].append(shift)

    user_hours: dict[int, float] = defaultdict(float)
    labor_by_day: dict[object, float] = defaultdict(float)
    total_labor = 0.0
    for shift in schedule_week.shifts:
        if shift.assigned_user_id:
            user_hours[shift.assigned_user_id] += float(shift.paid_hours or 0.0)
        labor_cost = float(shift.paid_hours or 0.0) * float(
            shift.hourly_rate_snapshot or 0.0
        )
        labor_by_day[shift.shift_date] += labor_cost
        total_labor += labor_cost

    receipts_by_user_id = {
        receipt.user_id: receipt for receipt in schedule_week.receipts
    }
    current_seen_count = sum(
        1
        for receipt in schedule_week.receipts
        if (receipt.last_seen_version or 0) >= (schedule_week.current_version or 0)
    )
    return {
        "schedule_week": schedule_week,
        "assignable_users": visible_users,
        "visible_users": visible_users,
        "week_dates": week_dates,
        "shifts_by_row": shifts_by_row,
        "open_shifts": open_shifts,
        "user_hours": user_hours,
        "labor_by_day": labor_by_day,
        "total_labor": total_labor,
        "receipts_by_user_id": receipts_by_user_id,
        "current_seen_count": current_seen_count,
    }


def _prepare_multi_department_schedule_context(
    departments: list[Department],
    week_start,
    *,
    include_self_only: bool,
    requested_user_id: int | None,
):
    normalized_week_start = normalize_week_start(week_start)
    week_dates = [normalized_week_start + timedelta(days=offset) for offset in range(7)]
    filter_users = _build_team_schedule_filter_users(
        departments,
        include_self_only=include_self_only,
    )
    visible_users, selected_user_id = _filter_schedule_users(
        filter_users,
        requested_user_id,
    )
    schedule_weeks = (
        DepartmentScheduleWeek.query.options(
            selectinload(DepartmentScheduleWeek.shifts)
            .selectinload(Shift.position)
            .selectinload(ShiftPosition.department),
            selectinload(DepartmentScheduleWeek.shifts).selectinload(
                Shift.assigned_user
            ),
        )
        .filter(
            DepartmentScheduleWeek.department_id.in_([department.id for department in departments]),
            DepartmentScheduleWeek.week_start == normalized_week_start,
        )
        .all()
    )
    shifts = sorted(
        [
            shift
            for schedule_week in schedule_weeks
            for shift in schedule_week.shifts
        ],
        key=lambda value: (
            value.shift_date,
            value.start_time,
            getattr(value.position.department, "name", "") if value.position else "",
            value.id,
        ),
    )
    shifts_by_row: dict[tuple[int, object], list[Shift]] = defaultdict(list)
    open_shifts: list[Shift] = []
    user_hours: dict[int, float] = defaultdict(float)
    labor_by_day: dict[object, float] = defaultdict(float)
    total_labor = 0.0

    for shift in shifts:
        if shift.assignment_mode != Shift.ASSIGNMENT_ASSIGNED or not shift.assigned_user_id:
            open_shifts.append(shift)
            shifts_by_row[(-1, shift.shift_date)].append(shift)
        else:
            shifts_by_row[(shift.assigned_user_id, shift.shift_date)].append(shift)
            user_hours[shift.assigned_user_id] += float(shift.paid_hours or 0.0)
        labor_cost = float(shift.paid_hours or 0.0) * float(
            shift.hourly_rate_snapshot or 0.0
        )
        labor_by_day[shift.shift_date] += labor_cost
        total_labor += labor_cost

    return {
        "schedule_week": SimpleNamespace(
            week_start=normalized_week_start,
            is_published=False,
            current_version=0,
            shifts=shifts,
        ),
        "assignable_users": filter_users,
        "visible_users": visible_users,
        "selected_user_id": selected_user_id,
        "week_dates": week_dates,
        "shifts_by_row": shifts_by_row,
        "open_shifts": open_shifts,
        "user_hours": user_hours,
        "labor_by_day": labor_by_day,
        "total_labor": total_labor,
        "receipts_by_user_id": {},
        "current_seen_count": 0,
    }


def _validate_manual_assignment(
    assigned_user: User | None,
    shift_date,
    start_time,
    end_time,
    *,
    exclude_shift_id: int | None = None,
) -> list[str]:
    errors: list[str] = []
    if assigned_user is None:
        return errors
    overlapping_shift = find_overlapping_shift(
        assigned_user.id,
        shift_date,
        start_time,
        end_time,
        exclude_shift_id=exclude_shift_id,
    )
    if overlapping_shift is not None:
        errors.append("Assigned user already has an overlapping shift.")
    for request_obj in assigned_user.time_off_requests:
        if time_off_overlaps(request_obj, shift_date, start_time, end_time):
            errors.append("Assigned user has approved time off during that shift.")
            break
    for override_obj in assigned_user.availability_overrides:
        if override_blocks_shift(override_obj, shift_date, start_time, end_time):
            errors.append("Assigned user has an unavailable override during that shift.")
            break
    return errors


@schedule.route("/schedules", methods=["GET", "POST"])
@login_required
def team_schedule():
    """Show and manage the weekly schedule board."""
    can_team_access, can_self_schedule = _team_schedule_access_mode()
    visible_departments = get_visible_departments(
        current_user,
        require_team_access=can_team_access or can_self_schedule,
    )
    auto_assignable_departments = _auto_assignable_departments(
        current_user,
        visible_departments,
    )
    view_mode = _parse_schedule_view_mode(request.values.get("view_mode"))
    requested_department_filter = _parse_department_filter_value(
        request.values.get("department_id") or request.values.get("shift-department_id")
    )
    all_departments_mode = requested_department_filter == ALL_DEPARTMENTS_VALUE
    selected_department = None if all_departments_mode else _select_department(
        visible_departments,
        requested_department_filter if isinstance(requested_department_filter, int) else None,
    )
    if selected_department is None:
        if all_departments_mode and visible_departments:
            pass
        else:
            flash("No scheduling departments are available for your account.", "warning")
            return render_template(
                "schedules/team_schedule.html",
                departments=[],
                selected_department=None,
                selected_department_filter_value="",
                selected_user_filter_value="",
                filter_users=[],
                all_departments_mode=False,
                show_department_names=False,
                show_seen_status=False,
                schedule_week=None,
                shift_form=None,
                action_form=CSRFOnlyForm(prefix="action"),
                week_dates=[],
                shifts_by_row={},
                visible_users=[],
                open_shifts=[],
                user_hours={},
                labor_by_day={},
                total_labor=0.0,
                receipts_by_user_id={},
                current_seen_count=0,
                week_label="",
                previous_week=None,
                next_week=None,
                view_mode=view_mode,
                selected_event_filter_value="",
                selected_location_filter_value="",
                schedule_events=[],
                event_filter_locations=[],
                position_board_days=[],
                board_total_labor=0.0,
                board_assigned_shift_count=0,
                board_open_shift_count=0,
                schedule_event_location_map={},
                can_team_access=can_team_access,
                can_self_schedule=can_self_schedule,
                can_auto_assign_selected_scope=False,
                auto_assign_action_label="Auto Assign",
            )
    week_start = normalize_week_start(
        request.values.get("week_start") or request.values.get("shift-week_start")
    )
    include_self_only = not can_team_access and can_self_schedule
    requested_user_id = None
    if view_mode == SCHEDULE_VIEW_USER:
        requested_user_id = _parse_int(
            request.values.get("user_id") or request.values.get("shift-user_id")
        )
    can_auto_assign_selected_scope = False
    auto_assign_action_label = "Auto Assign"

    if all_departments_mode and visible_departments:
        context = _prepare_multi_department_schedule_context(
            visible_departments,
            week_start,
            include_self_only=include_self_only,
            requested_user_id=requested_user_id,
        )
        schedule_week = context["schedule_week"]
        shift_form = None
        action_form = CSRFOnlyForm(prefix="action")
        selected_user_id = (
            context["selected_user_id"] if view_mode == SCHEDULE_VIEW_USER else None
        )
        filter_users = context["assignable_users"]
        can_auto_assign_selected_scope = bool(auto_assignable_departments)
        auto_assign_action_label = "Auto Assign Allowed Departments"
    else:
        context = _prepare_schedule_week_context(
            selected_department,
            week_start,
            include_self_only=include_self_only,
        )
        filter_users = context["assignable_users"]
        if view_mode == SCHEDULE_VIEW_USER:
            filtered_visible_users, selected_user_id = _filter_schedule_users(
                context["visible_users"],
                requested_user_id,
            )
            context["visible_users"] = filtered_visible_users
            context["current_seen_count"] = _current_seen_count_for_users(
                context["receipts_by_user_id"],
                context["schedule_week"].current_version or 0,
                filtered_visible_users,
            )
        else:
            selected_user_id = None
        schedule_week = context["schedule_week"]
        shift_form = ShiftForm(prefix="shift", department_id=selected_department.id)
        action_form = CSRFOnlyForm(prefix="action")
        can_auto_assign_selected_scope = user_can_auto_assign_department(
            current_user,
            selected_department.id,
        )

    schedule_events = _load_schedule_events_for_week(schedule_week.week_start)
    selected_filter_event = _select_schedule_event(
        schedule_events,
        _parse_int(
            request.values.get("filter_event_id")
            or request.values.get("shift-filter_event_id")
        ),
    )
    event_filter_locations = _sorted_event_locations(selected_filter_event)
    requested_filter_location_id = _parse_int(
        request.values.get("filter_location_id")
        or request.values.get("shift-filter_location_id")
    )
    selected_filter_location_id = None
    if requested_filter_location_id and any(
        event_location.location_id == requested_filter_location_id
        for event_location in event_filter_locations
    ):
        selected_filter_location_id = requested_filter_location_id

    schedule_event_location_map: dict[str, list[dict[str, str | int]]] = {}
    if shift_form is not None:
        schedule_event_location_map = _build_schedule_event_location_map(
            Event.query.options(
                selectinload(Event.locations).selectinload(EventLocation.location),
            )
            .order_by(Event.start_date.desc(), Event.name.asc())
            .all()
        )

    position_board_shifts: list[Shift] = []
    position_board_days: list[dict] = []
    board_total_labor = context["total_labor"]
    board_assigned_shift_count = sum(
        1
        for shift in schedule_week.shifts
        if shift.assignment_mode == Shift.ASSIGNMENT_ASSIGNED and shift.assigned_user_id
    )
    board_open_shift_count = len(context["open_shifts"])
    if view_mode == SCHEDULE_VIEW_POSITION:
        position_board_shifts = _filter_shifts_for_position_view(
            list(schedule_week.shifts),
            week_dates=context["week_dates"],
            visible_user_ids={user.id for user in context["visible_users"]},
            selected_event_id=selected_filter_event.id if selected_filter_event else None,
            selected_location_id=selected_filter_location_id,
        )
        position_board_days = _build_position_schedule_days(
            position_board_shifts,
            week_dates=context["week_dates"],
            show_department_names=all_departments_mode,
        )
        board_total_labor = sum(
            float(shift.paid_hours or 0.0) * float(shift.hourly_rate_snapshot or 0.0)
            for shift in position_board_shifts
        )
        board_assigned_shift_count = sum(
            1
            for shift in position_board_shifts
            if shift.assignment_mode == Shift.ASSIGNMENT_ASSIGNED
            and shift.assigned_user_id
        )
        board_open_shift_count = len(position_board_shifts) - board_assigned_shift_count

    if request.method == "POST" and all_departments_mode:
        action = (request.form.get("action") or "").strip()
        if action != "auto_assign":
            abort(400)

    if selected_department is None and not all_departments_mode:
        flash("No scheduling departments are available for your account.", "warning")
        return render_template(
            "schedules/team_schedule.html",
            departments=[],
            selected_department=None,
            selected_department_filter_value="",
            selected_user_filter_value="",
            filter_users=[],
            all_departments_mode=False,
            show_department_names=False,
            show_seen_status=False,
            schedule_week=None,
            shift_form=None,
            action_form=CSRFOnlyForm(prefix="action"),
            week_dates=[],
            shifts_by_row={},
            visible_users=[],
            open_shifts=[],
            user_hours={},
            labor_by_day={},
            total_labor=0.0,
            receipts_by_user_id={},
            current_seen_count=0,
            week_label="",
            previous_week=None,
            next_week=None,
            view_mode=view_mode,
            selected_event_filter_value=str(
                selected_filter_event.id if selected_filter_event else ""
            ),
            selected_location_filter_value=str(selected_filter_location_id or ""),
            schedule_events=schedule_events,
            event_filter_locations=event_filter_locations,
            position_board_days=position_board_days,
            board_total_labor=board_total_labor,
            board_assigned_shift_count=board_assigned_shift_count,
            board_open_shift_count=board_open_shift_count,
            schedule_event_location_map=schedule_event_location_map,
            can_team_access=can_team_access,
            can_self_schedule=can_self_schedule,
            can_auto_assign_selected_scope=False,
            auto_assign_action_label="Auto Assign",
        )

    if request.method == "POST":
        action = (request.form.get("action") or "").strip()
        redirect_kwargs = {
            "user_id": selected_user_id if view_mode == SCHEDULE_VIEW_USER else None,
            "view_mode": view_mode,
            "filter_event_id": (
                selected_filter_event.id
                if view_mode == SCHEDULE_VIEW_POSITION and selected_filter_event
                else None
            ),
            "filter_location_id": (
                selected_filter_location_id
                if view_mode == SCHEDULE_VIEW_POSITION
                else None
            ),
        }
        if action == "save_shift":
            if not (can_team_access or can_self_schedule):
                abort(403)
            if shift_form.validate_on_submit():
                shift_id = _parse_int(shift_form.shift_id.data)
                existing_shift = db.session.get(Shift, shift_id) if shift_id else None
                if (
                    existing_shift is not None
                    and existing_shift.schedule_week_id != schedule_week.id
                ):
                    abort(400)

                position = db.session.get(ShiftPosition, shift_form.position_id.data)
                if position is None or position.department_id != selected_department.id:
                    shift_form.position_id.errors.append(
                        "Selected position does not belong to this department."
                    )

                selected_event_id = _parse_int(shift_form.event_id.data)
                selected_location_id = _parse_int(shift_form.location_id.data)
                selected_event = None
                if selected_event_id:
                    selected_event = (
                        Event.query.options(
                            selectinload(Event.locations).selectinload(
                                EventLocation.location
                            ),
                        )
                        .filter_by(id=selected_event_id)
                        .first()
                    )
                    if selected_event is None:
                        shift_form.event_id.errors.append("Selected event is invalid.")
                if (
                    selected_event is not None
                    and selected_location_id
                    and not any(
                        event_location.location_id == selected_location_id
                        for event_location in selected_event.locations
                    )
                ):
                    shift_form.location_id.errors.append(
                        "Selected location is not assigned to the chosen event."
                    )

                assigned_user_id = (
                    shift_form.assigned_user_id.data
                    if shift_form.assigned_user_id.data not in (None, 0)
                    else None
                )
                assignment_mode = shift_form.assignment_mode.data
                if assignment_mode == Shift.ASSIGNMENT_ASSIGNED and not assigned_user_id:
                    shift_form.assigned_user_id.errors.append(
                        "Assigned shifts require a user."
                    )

                assigned_user = (
                    db.session.get(User, assigned_user_id) if assigned_user_id else None
                )
                visible_user_ids = {user.id for user in context["assignable_users"]}
                if assigned_user is not None and assigned_user.id not in visible_user_ids:
                    shift_form.assigned_user_id.errors.append(
                        "Assigned user is outside your scheduling scope."
                    )
                if not can_team_access:
                    if assignment_mode != Shift.ASSIGNMENT_ASSIGNED:
                        shift_form.assignment_mode.errors.append(
                            "Self scheduling only supports assigned shifts."
                        )
                    if assigned_user_id != current_user.id:
                        shift_form.assigned_user_id.errors.append(
                            "You can only schedule shifts for yourself."
                        )
                    if existing_shift and existing_shift.assigned_user_id not in (
                        None,
                        current_user.id,
                    ):
                        abort(403)

                target_dates: list = [shift_form.shift_date.data]
                copy_count = 1
                if existing_shift is not None:
                    if shift_form.shift_date.data not in context["week_dates"]:
                        shift_form.shift_date.errors.append(
                            "Shift date must be within the selected week."
                        )
                else:
                    selected_weekdays = sorted(set(shift_form.target_days.data or []))
                    if not selected_weekdays:
                        if shift_form.shift_date.data in context["week_dates"]:
                            selected_weekdays = [
                                (shift_form.shift_date.data.weekday()) % 7
                            ]
                        else:
                            shift_form.target_days.errors.append(
                                "Select at least one day for a new shift."
                            )
                    copy_count = int(shift_form.copy_count.data or 1)
                    repeat_weeks = int(shift_form.repeat_weeks.data or 0)
                    target_dates = [
                        schedule_week.week_start + timedelta(days=weekday + (week_offset * 7))
                        for week_offset in range(repeat_weeks + 1)
                        for weekday in selected_weekdays
                    ]
                    if (
                        assignment_mode == Shift.ASSIGNMENT_ASSIGNED
                        and assigned_user_id
                        and copy_count > 1
                    ):
                        shift_form.copy_count.errors.append(
                            "Assigned shifts can only create one copy per selected day."
                        )

                if selected_event is not None and not shift_form.errors:
                    invalid_event_dates = [
                        target_date
                        for target_date in target_dates
                        if target_date < selected_event.start_date
                        or target_date > selected_event.end_date
                    ]
                    if invalid_event_dates:
                        shift_form.event_id.errors.append(
                            "Selected event does not cover every chosen shift date."
                        )

                if not shift_form.errors:
                    for target_date in sorted(set(target_dates)):
                        manual_errors = _validate_manual_assignment(
                            assigned_user
                            if assignment_mode == Shift.ASSIGNMENT_ASSIGNED
                            else None,
                            target_date,
                            shift_form.start_time.data,
                            shift_form.end_time.data,
                            exclude_shift_id=existing_shift.id if existing_shift else None,
                        )
                        for error in manual_errors:
                            shift_form.start_time.errors.append(error)

                if not shift_form.errors:
                    if (
                        shift_form.paid_hours_manual.data
                        and shift_form.paid_hours.data is not None
                    ):
                        effective_paid_hours = float(shift_form.paid_hours.data or 0.0)
                    else:
                        effective_paid_hours = round(
                            (
                                datetime.combine(
                                    datetime.utcnow().date(), shift_form.end_time.data
                                )
                                - datetime.combine(
                                    datetime.utcnow().date(), shift_form.start_time.data
                                )
                            ).total_seconds()
                            / 3600.0,
                            2,
                        )

                    schedule_weeks_by_start = {schedule_week.week_start: schedule_week}
                    changes_by_week: dict[object, dict[str, object]] = {}
                    target_dates_with_copies = [
                        target_date
                        for target_date in target_dates
                        for _copy_index in range(copy_count)
                    ]

                    for target_date in target_dates_with_copies:
                        if existing_shift is not None:
                            target_schedule_week = schedule_week
                            shift = existing_shift
                            action_name = "updated"
                            before = capture_shift_snapshot(shift)
                        else:
                            target_week_start = normalize_week_start(target_date)
                            target_schedule_week = schedule_weeks_by_start.get(
                                target_week_start
                            )
                            if target_schedule_week is None:
                                target_schedule_week = get_or_create_schedule_week(
                                    selected_department.id,
                                    target_week_start,
                                )
                                db.session.flush()
                                schedule_weeks_by_start[target_week_start] = (
                                    target_schedule_week
                                )
                            shift = Shift(
                                schedule_week=target_schedule_week,
                                created_by=current_user,
                            )
                            action_name = "created"
                            before = None
                            db.session.add(shift)
                        shift.position = position
                        shift.shift_date = target_date
                        shift.start_time = shift_form.start_time.data
                        shift.end_time = shift_form.end_time.data
                        shift.notes = (shift_form.notes.data or "").strip() or None
                        shift.color = (shift_form.color.data or "").strip() or None
                        shift.location_id = selected_location_id
                        shift.event_id = selected_event_id
                        shift.assignment_mode = assignment_mode
                        shift.is_locked = bool(shift_form.is_locked.data)
                        shift.paid_hours_manual = bool(shift_form.paid_hours_manual.data)
                        shift.paid_hours = round(float(effective_paid_hours or 0.0), 2)
                        shift.updated_by = current_user
                        if assignment_mode == Shift.ASSIGNMENT_ASSIGNED:
                            shift.assigned_user = assigned_user
                        else:
                            shift.assigned_user = None
                        apply_rate_snapshot(shift)
                        after = capture_shift_snapshot(shift)
                        week_entry = changes_by_week.setdefault(
                            target_schedule_week.week_start,
                            {
                                "schedule_week": target_schedule_week,
                                "change_records": [],
                                "touched_shifts": [],
                            },
                        )
                        week_entry["change_records"].append((before, shift))
                        week_entry["touched_shifts"].append(
                            (shift, action_name, before, after)
                        )
                        existing_shift = None

                    published_notifications: list[tuple[DepartmentScheduleWeek, list]] = []
                    total_saved = 0
                    for week_entry in changes_by_week.values():
                        target_schedule_week = week_entry["schedule_week"]
                        change_records = week_entry["change_records"]
                        touched_shifts = week_entry["touched_shifts"]
                        total_saved += len(touched_shifts)
                        published_material_changes = [
                            (before, shift)
                            for before, shift in change_records
                            if before is None
                            or material_change_fields(
                                before,
                                capture_shift_snapshot(shift),
                            )
                        ]
                        if target_schedule_week.is_published and published_material_changes:
                            target_schedule_week.current_version += 1
                            for shift, _action_name, _before, _after in touched_shifts:
                                shift.live_version = target_schedule_week.current_version
                            published_notifications.append(
                                (target_schedule_week, published_material_changes)
                            )

                    for week_entry in changes_by_week.values():
                        target_schedule_week = week_entry["schedule_week"]
                        for shift, action_name, before, after in week_entry[
                            "touched_shifts"
                        ]:
                            record_shift_audit(
                                shift,
                                actor=current_user,
                                action=action_name,
                                version=shift.live_version
                                or target_schedule_week.current_version
                                or 0,
                                before=before,
                                after=after,
                                summary=(
                                    f"{'Updated' if action_name == 'updated' else 'Created'} "
                                    f"shift for {shift.shift_date}."
                                ),
                            )
                    db.session.commit()
                    for target_schedule_week, published_material_changes in (
                        published_notifications
                    ):
                        notify_schedule_changes(
                            target_schedule_week,
                            published_material_changes,
                        )
                    flash(
                        "Shift saved." if total_saved == 1 else f"{total_saved} shifts saved.",
                        "success",
                    )
                    log_schedule_action(
                        f"Saved {total_saved} schedule shift(s) for department {selected_department.name}"
                    )
                    return redirect(
                        _schedule_redirect(
                            "schedule.team_schedule",
                            selected_department.id,
                            schedule_week.week_start,
                            **redirect_kwargs,
                        )
                    )
            flash("Unable to save shift. Please review the form.", "danger")
        elif action == "delete_shift":
            if not current_user.has_permission("schedules.delete"):
                abort(403)
            shift = db.session.get(Shift, _parse_int(request.form.get("shift_id")))
            if shift is None or shift.schedule_week_id != schedule_week.id:
                abort(404)
            if schedule_week.is_published:
                flash(
                    "Published shifts cannot be deleted. Unpublish the week first.",
                    "warning",
                )
            else:
                if not can_team_access and shift.assigned_user_id != current_user.id:
                    abort(403)
                db.session.delete(shift)
                db.session.commit()
                flash("Shift deleted.", "success")
                log_schedule_action(
                    f"Deleted schedule shift {shift.id} in department {selected_department.name}"
                )
            return redirect(
                _schedule_redirect(
                    "schedule.team_schedule",
                    selected_department.id,
                    schedule_week.week_start,
                    **redirect_kwargs,
                )
            )
        elif action == "publish_week":
            if not current_user.has_permission("schedules.publish"):
                abort(403)
            if not user_can_manage_department(current_user, selected_department.id):
                abort(403)
            if not schedule_week.is_published:
                schedule_week.is_published = True
                schedule_week.published_at = datetime.utcnow()
                schedule_week.unpublished_at = None
                schedule_week.published_by = current_user
                schedule_week.current_version += 1
                for shift in schedule_week.shifts:
                    apply_rate_snapshot(shift)
                    shift.live_version = schedule_week.current_version
                db.session.commit()
                notify_schedule_posted(schedule_week, schedule_week.shifts)
                flash("Schedule week published.", "success")
                log_schedule_action(
                    f"Published schedule week {schedule_week.week_start} for department {selected_department.name}"
                )
            return redirect(
                _schedule_redirect(
                    "schedule.team_schedule",
                    selected_department.id,
                    schedule_week.week_start,
                    **redirect_kwargs,
                )
            )
        elif action == "unpublish_week":
            if not current_user.has_permission("schedules.publish"):
                abort(403)
            if not user_can_manage_department(current_user, selected_department.id):
                abort(403)
            if schedule_week.is_published:
                schedule_week.is_published = False
                schedule_week.unpublished_at = datetime.utcnow()
                db.session.commit()
                flash("Schedule week unpublished.", "success")
                log_schedule_action(
                    f"Unpublished schedule week {schedule_week.week_start} for department {selected_department.name}"
                )
            return redirect(
                _schedule_redirect(
                    "schedule.team_schedule",
                    selected_department.id,
                    schedule_week.week_start,
                    **redirect_kwargs,
                )
            )
        elif action == "auto_assign":
            if all_departments_mode:
                if not auto_assignable_departments:
                    abort(403)
                allowed_department_ids = [
                    department.id for department in auto_assignable_departments
                ]
                schedule_weeks = (
                    DepartmentScheduleWeek.query.filter(
                        DepartmentScheduleWeek.department_id.in_(
                            allowed_department_ids
                        ),
                        DepartmentScheduleWeek.week_start == schedule_week.week_start,
                    )
                    .all()
                )
                weeks_by_department_id = {
                    week.department_id: week for week in schedule_weeks
                }
                results = []
                processed_departments: list[str] = []
                for department in auto_assignable_departments:
                    target_week = weeks_by_department_id.get(department.id)
                    if target_week is None:
                        continue
                    processed_departments.append(department.name)
                    results.extend(auto_assign_shifts(target_week, actor=current_user))
                db.session.commit()
                assigned_count, unassigned_count, reason_note = (
                    _auto_assign_result_summary(results)
                )
                processed_note = ""
                if processed_departments:
                    processed_note = (
                        f" Processed {len(processed_departments)} department(s)"
                    )
                    if len(processed_departments) <= 3:
                        processed_note += f": {', '.join(processed_departments)}."
                    else:
                        processed_note += "."
                else:
                    processed_note = " No schedule weeks were available for your auto-assign departments."
                flash(
                    f"Auto-assign complete.{processed_note} {assigned_count} shifts assigned, "
                    f"{unassigned_count} left unassigned.{reason_note}",
                    "success" if assigned_count else "warning",
                )
                log_schedule_action(
                    f"Ran auto-assign for {len(processed_departments)} departments week {schedule_week.week_start}"
                )
                return redirect(
                    _schedule_redirect(
                        "schedule.team_schedule",
                        ALL_DEPARTMENTS_VALUE,
                        schedule_week.week_start,
                        **redirect_kwargs,
                    )
                )

            if not user_can_auto_assign_department(current_user, selected_department.id):
                abort(403)
            results = auto_assign_shifts(schedule_week, actor=current_user)
            db.session.commit()
            assigned_count, unassigned_count, reason_note = _auto_assign_result_summary(
                results
            )
            flash(
                f"Auto-assign complete. {assigned_count} shifts assigned, "
                f"{unassigned_count} left unassigned.{reason_note}",
                "success" if assigned_count else "warning",
            )
            log_schedule_action(
                f"Ran auto-assign for department {selected_department.name} week {schedule_week.week_start}"
            )
            return redirect(
                _schedule_redirect(
                    "schedule.team_schedule",
                    selected_department.id,
                    schedule_week.week_start,
                    **redirect_kwargs,
                )
            )

    if (
        selected_department is not None
        and schedule_week.is_published
        and any(
        membership.department_id == selected_department.id
        for membership in current_user.department_memberships
        )
    ):
        mark_schedule_week_seen(current_user, [schedule_week])
        db.session.commit()

    return render_template(
        "schedules/team_schedule.html",
        departments=visible_departments,
        selected_department=selected_department,
        selected_department_filter_value=(
            ALL_DEPARTMENTS_VALUE
            if all_departments_mode
            else str(selected_department.id)
        ),
        selected_user_filter_value=str(selected_user_id or ""),
        filter_users=filter_users,
        all_departments_mode=all_departments_mode,
        show_department_names=all_departments_mode,
        show_seen_status=selected_department is not None,
        schedule_week=schedule_week,
        shift_form=shift_form,
        action_form=action_form,
        week_dates=context["week_dates"],
        shifts_by_row=context["shifts_by_row"],
        visible_users=context["visible_users"],
        open_shifts=context["open_shifts"],
        user_hours=context["user_hours"],
        labor_by_day=context["labor_by_day"],
        total_labor=context["total_labor"],
        receipts_by_user_id=context["receipts_by_user_id"],
        current_seen_count=context["current_seen_count"],
        week_label=format_week_label(schedule_week.week_start),
        previous_week=schedule_week.week_start - timedelta(days=7),
        next_week=schedule_week.week_start + timedelta(days=7),
        view_mode=view_mode,
        selected_event_filter_value=str(selected_filter_event.id if selected_filter_event else ""),
        selected_location_filter_value=str(selected_filter_location_id or ""),
        schedule_events=schedule_events,
        event_filter_locations=event_filter_locations,
        position_board_days=position_board_days,
        board_total_labor=board_total_labor,
        board_assigned_shift_count=board_assigned_shift_count,
        board_open_shift_count=board_open_shift_count,
        schedule_event_location_map=schedule_event_location_map,
        can_team_access=can_team_access,
        can_self_schedule=can_self_schedule,
        can_auto_assign_selected_scope=can_auto_assign_selected_scope,
        auto_assign_action_label=auto_assign_action_label,
    )


@schedule.route("/schedules/mine", methods=["GET"])
@login_required
def my_schedule():
    """Show the current user's published schedule."""
    departments = [
        department
        for department in get_visible_departments(current_user)
        if any(
            membership.department_id == department.id
            for membership in current_user.department_memberships
        )
    ]
    selected_department = _select_department(
        departments, _parse_int(request.args.get("department_id"))
    )
    week_start = normalize_week_start(request.args.get("week_start"))
    schedule_week = None
    shifts: list[Shift] = []
    receipt = None
    if selected_department is not None:
        schedule_week = (
            DepartmentScheduleWeek.query.options(
                selectinload(DepartmentScheduleWeek.shifts).selectinload(Shift.position),
                selectinload(DepartmentScheduleWeek.receipts),
            )
            .filter_by(
                department_id=selected_department.id,
                week_start=week_start,
                is_published=True,
            )
            .first()
        )
        if schedule_week is not None:
            mark_schedule_week_seen(current_user, [schedule_week])
            db.session.commit()
            receipt = next(
                (
                    item
                    for item in schedule_week.receipts
                    if item.user_id == current_user.id
                ),
                None,
            )
            shifts = [
                shift
                for shift in schedule_week.shifts
                if shift.assigned_user_id == current_user.id
            ]
    return render_template(
        "schedules/my_schedule.html",
        departments=departments,
        selected_department=selected_department,
        schedule_week=schedule_week,
        week_label=format_week_label(week_start),
        previous_week=week_start - timedelta(days=7),
        next_week=week_start + timedelta(days=7),
        shifts=sorted(shifts, key=lambda shift: (shift.shift_date, shift.start_time)),
        receipt=receipt,
    )


@schedule.route("/schedules/availability", methods=["GET", "POST"])
@login_required
def availability():
    """Manage recurring availability and date-specific overrides."""
    manageable_users = _manager_scope_users(current_user)
    requested_user_id = _parse_int(
        request.values.get("user_id")
        or request.values.get("window-user_id")
        or request.values.get("override-user_id")
    )
    target_user = current_user
    if requested_user_id and requested_user_id != current_user.id:
        requested_user = db.session.get(User, requested_user_id)
        if requested_user is None:
            abort(404)
        if not current_user.has_permission("schedules.manage_team_availability"):
            abort(403)
        if not _can_manage_user_in_any_department(current_user, requested_user):
            abort(403)
        target_user = requested_user

    window_form = AvailabilityWindowForm(prefix="window")
    override_form = AvailabilityOverrideForm(prefix="override")
    action_form = CSRFOnlyForm(prefix="availability")

    if request.method == "POST":
        action = (request.form.get("action") or "").strip()
        if action == "add_window":
            if target_user.id != current_user.id and not current_user.has_permission(
                "schedules.manage_team_availability"
            ):
                abort(403)
            if target_user.id == current_user.id and not current_user.has_any_permission(
                "schedules.manage_self_availability",
                "schedules.manage_team_availability",
            ):
                abort(403)
            if window_form.validate_on_submit():
                db.session.add(
                    RecurringAvailabilityWindow(
                        user=target_user,
                        weekday=window_form.weekday.data,
                        start_time=window_form.start_time.data,
                        end_time=window_form.end_time.data,
                        note=(window_form.note.data or "").strip() or None,
                    )
                )
                db.session.commit()
                flash("Availability window added.", "success")
                return redirect(
                    url_for("schedule.availability", user_id=target_user.id)
                )
        elif action == "delete_window":
            window = db.session.get(
                RecurringAvailabilityWindow,
                _parse_int(request.form.get("window_id")),
            )
            if window is None or window.user_id != target_user.id:
                abort(404)
            db.session.delete(window)
            db.session.commit()
            flash("Availability window deleted.", "success")
            return redirect(url_for("schedule.availability", user_id=target_user.id))
        elif action == "add_override":
            if override_form.validate_on_submit():
                db.session.add(
                    AvailabilityOverride(
                        user=target_user,
                        start_at=override_form.start_at.data,
                        end_at=override_form.end_at.data,
                        is_available=bool(override_form.is_available.data),
                        note=(override_form.note.data or "").strip() or None,
                    )
                )
                db.session.commit()
                flash("Availability override added.", "success")
                return redirect(url_for("schedule.availability", user_id=target_user.id))
        elif action == "delete_override":
            override_item = db.session.get(
                AvailabilityOverride,
                _parse_int(request.form.get("override_id")),
            )
            if override_item is None or override_item.user_id != target_user.id:
                abort(404)
            db.session.delete(override_item)
            db.session.commit()
            flash("Availability override deleted.", "success")
            return redirect(url_for("schedule.availability", user_id=target_user.id))

    windows = (
        RecurringAvailabilityWindow.query.filter_by(user_id=target_user.id)
        .order_by(
            RecurringAvailabilityWindow.weekday.asc(),
            RecurringAvailabilityWindow.start_time.asc(),
        )
        .all()
    )
    overrides = (
        AvailabilityOverride.query.filter_by(user_id=target_user.id)
        .order_by(AvailabilityOverride.start_at.asc())
        .all()
    )
    weekday_labels = {
        0: "Monday",
        1: "Tuesday",
        2: "Wednesday",
        3: "Thursday",
        4: "Friday",
        5: "Saturday",
        6: "Sunday",
    }
    return render_template(
        "schedules/availability.html",
        target_user=target_user,
        manageable_users=manageable_users,
        window_form=window_form,
        override_form=override_form,
        action_form=action_form,
        windows=windows,
        overrides=overrides,
        weekday_labels=weekday_labels,
    )


@schedule.route("/schedules/time-off", methods=["GET", "POST"])
@login_required
def time_off():
    """Submit and review time-off requests."""
    request_form = TimeOffRequestForm(prefix="request")
    review_form = TimeOffReviewForm(prefix="review")
    action_form = CSRFOnlyForm(prefix="timeoff")
    manageable_users = _manager_scope_users(current_user)

    if request.method == "POST":
        action = (request.form.get("action") or "").strip()
        if action == "submit_request":
            if not current_user.has_permission("schedules.request_time_off"):
                abort(403)
            if request_form.validate_on_submit():
                time_off_request = TimeOffRequest(
                    user=current_user,
                    start_date=request_form.start_date.data,
                    end_date=request_form.end_date.data,
                    start_time=request_form.start_time.data,
                    end_time=request_form.end_time.data,
                    reason=(request_form.reason.data or "").strip(),
                )
                db.session.add(time_off_request)
                db.session.commit()
                notify_time_off_submitted(time_off_request)
                flash("Time-off request submitted.", "success")
                return redirect(url_for("schedule.time_off"))
        elif action == "cancel_request":
            request_obj = db.session.get(
                TimeOffRequest, _parse_int(request.form.get("request_id"))
            )
            if request_obj is None or request_obj.user_id != current_user.id:
                abort(404)
            if request_obj.status != TimeOffRequest.STATUS_PENDING:
                flash("Only pending requests can be cancelled.", "warning")
            else:
                request_obj.status = TimeOffRequest.STATUS_CANCELLED
                db.session.commit()
                flash("Time-off request cancelled.", "success")
            return redirect(url_for("schedule.time_off"))
        elif action == "review_request":
            if not current_user.has_permission("schedules.approve_time_off"):
                abort(403)
            request_obj = db.session.get(
                TimeOffRequest, _parse_int(request.form.get("request_id"))
            )
            if request_obj is None:
                abort(404)
            if not _can_manage_user_in_any_department(current_user, request_obj.user):
                abort(403)
            if review_form.validate_on_submit():
                request_obj.status = review_form.status.data
                request_obj.manager_note = (
                    review_form.manager_note.data or ""
                ).strip() or None
                request_obj.reviewed_by = current_user
                request_obj.reviewed_at = datetime.utcnow()
                db.session.commit()
                notify_time_off_reviewed(request_obj)
                flash("Time-off request updated.", "success")
                return redirect(url_for("schedule.time_off"))

    own_requests = (
        TimeOffRequest.query.filter_by(user_id=current_user.id)
        .order_by(TimeOffRequest.created_at.desc())
        .all()
    )
    team_requests = (
        TimeOffRequest.query.options(selectinload(TimeOffRequest.user))
        .order_by(
            TimeOffRequest.status.asc(),
            TimeOffRequest.start_date.asc(),
            TimeOffRequest.created_at.desc(),
        )
        .all()
    )
    if not (
        getattr(current_user, "is_super_admin", False)
        or user_is_schedule_gm(current_user)
    ):
        managed_user_ids = {user.id for user in manageable_users}
        team_requests = [
            request_obj
            for request_obj in team_requests
            if request_obj.user_id in managed_user_ids
        ]

    selected_review_request = None
    review_request_id = _parse_int(request.args.get("review_id"))
    if review_request_id:
        selected_review_request = next(
            (
                request_obj
                for request_obj in team_requests
                if request_obj.id == review_request_id
            ),
            None,
        )

    return render_template(
        "schedules/time_off.html",
        request_form=request_form,
        review_form=review_form,
        action_form=action_form,
        own_requests=own_requests,
        team_requests=team_requests,
        selected_review_request=selected_review_request,
    )


@schedule.route("/schedules/tradeboard", methods=["GET", "POST"])
@login_required
def tradeboard():
    """View and claim tradeboard/open shifts."""
    manageable_departments = get_visible_departments(current_user)
    selected_department = _select_department(
        manageable_departments,
        _parse_int(request.values.get("department_id")),
    )
    week_start = normalize_week_start(request.values.get("week_start"))
    review_form = TradeboardClaimReviewForm(prefix="claimreview")
    action_form = CSRFOnlyForm(prefix="tradeboard")

    schedule_week = None
    shifts: list[Shift] = []
    pending_claims: list[TradeboardClaim] = []
    if selected_department is not None:
        schedule_week = (
            DepartmentScheduleWeek.query.options(
                selectinload(DepartmentScheduleWeek.shifts).selectinload(Shift.position),
                selectinload(DepartmentScheduleWeek.shifts)
                .selectinload(Shift.tradeboard_claims)
                .selectinload(TradeboardClaim.user),
            )
            .filter_by(
                department_id=selected_department.id,
                week_start=week_start,
                is_published=True,
            )
            .first()
        )
        if schedule_week is not None:
            eligible_position_ids = {
                eligibility.position_id
                for eligibility in current_user.position_eligibilities
                if eligibility.active
                and eligibility.position.department_id == selected_department.id
            }
            can_manage_claims = current_user.has_permission(
                "schedules.approve_tradeboard"
            )
            for shift in schedule_week.shifts:
                if shift.assignment_mode not in (
                    Shift.ASSIGNMENT_OPEN,
                    Shift.ASSIGNMENT_TRADEBOARD,
                ):
                    continue
                if can_manage_claims or shift.position_id in eligible_position_ids:
                    shifts.append(shift)
                for claim in shift.tradeboard_claims:
                    if claim.status == TradeboardClaim.STATUS_PENDING and (
                        can_manage_claims
                        and _can_manage_user_in_any_department(current_user, claim.user)
                    ):
                        pending_claims.append(claim)

    if request.method == "POST":
        action = (request.form.get("action") or "").strip()
        if action == "claim_shift":
            if not current_user.has_permission("schedules.claim_tradeboard"):
                abort(403)
            shift = db.session.get(Shift, _parse_int(request.form.get("shift_id")))
            if shift is None or shift.schedule_week is None or not shift.schedule_week.is_published:
                abort(404)
            if shift.assignment_mode not in (
                Shift.ASSIGNMENT_OPEN,
                Shift.ASSIGNMENT_TRADEBOARD,
            ):
                abort(400)
            existing_claim = TradeboardClaim.query.filter_by(
                shift_id=shift.id,
                user_id=current_user.id,
            ).first()
            if existing_claim is None:
                db.session.add(
                    TradeboardClaim(
                        shift=shift,
                        user=current_user,
                        status=TradeboardClaim.STATUS_PENDING,
                    )
                )
            elif existing_claim.status == TradeboardClaim.STATUS_CANCELLED:
                existing_claim.status = TradeboardClaim.STATUS_PENDING
                existing_claim.reviewed_by = None
                existing_claim.reviewed_at = None
                existing_claim.manager_note = None
            else:
                flash("You already have a claim for that shift.", "warning")
                return redirect(
                    _schedule_redirect(
                        "schedule.tradeboard",
                        shift.schedule_week.department_id,
                        shift.schedule_week.week_start,
                    )
                )
            db.session.commit()
            flash("Shift claim submitted.", "success")
            return redirect(
                _schedule_redirect(
                    "schedule.tradeboard",
                    shift.schedule_week.department_id,
                    shift.schedule_week.week_start,
                )
            )
        elif action == "cancel_claim":
            claim = db.session.get(
                TradeboardClaim, _parse_int(request.form.get("claim_id"))
            )
            if claim is None or claim.user_id != current_user.id:
                abort(404)
            if claim.status == TradeboardClaim.STATUS_PENDING:
                claim.status = TradeboardClaim.STATUS_CANCELLED
                db.session.commit()
                flash("Shift claim cancelled.", "success")
            return redirect(url_for("schedule.tradeboard"))
        elif action == "review_claim":
            if not current_user.has_permission("schedules.approve_tradeboard"):
                abort(403)
            claim = db.session.get(
                TradeboardClaim, _parse_int(request.form.get("claim_id"))
            )
            if claim is None:
                abort(404)
            if not _can_manage_user_in_any_department(current_user, claim.user):
                abort(403)
            if review_form.validate_on_submit():
                shift = claim.shift
                claim.status = review_form.status.data
                claim.manager_note = (
                    review_form.manager_note.data or ""
                ).strip() or None
                claim.reviewed_by = current_user
                claim.reviewed_at = datetime.utcnow()
                change_records: list[tuple[dict | None, Shift]] = []
                if claim.status == TradeboardClaim.STATUS_APPROVED:
                    before = capture_shift_snapshot(shift)
                    shift.assigned_user = claim.user
                    shift.assignment_mode = Shift.ASSIGNMENT_ASSIGNED
                    apply_rate_snapshot(shift)
                    shift.updated_by = current_user
                    shift.schedule_week.current_version += 1
                    shift.live_version = shift.schedule_week.current_version
                    change_records.append((before, shift))
                    for sibling in shift.tradeboard_claims:
                        if sibling.id != claim.id and sibling.status == TradeboardClaim.STATUS_PENDING:
                            sibling.status = TradeboardClaim.STATUS_REJECTED
                            sibling.reviewed_by = current_user
                            sibling.reviewed_at = datetime.utcnow()
                            sibling.manager_note = "Another claim was approved."
                    record_shift_audit(
                        shift,
                        actor=current_user,
                        action="tradeboard_claim_approved",
                        version=shift.live_version,
                        before=before,
                        after=capture_shift_snapshot(shift),
                        summary=f"Approved tradeboard claim for {claim.user.email}.",
                    )
                db.session.commit()
                if change_records:
                    notify_schedule_changes(shift.schedule_week, change_records)
                flash("Tradeboard claim updated.", "success")
                return redirect(
                    _schedule_redirect(
                        "schedule.tradeboard",
                        shift.schedule_week.department_id,
                        shift.schedule_week.week_start,
                    )
                )

    return render_template(
        "schedules/tradeboard.html",
        departments=manageable_departments,
        selected_department=selected_department,
        schedule_week=schedule_week,
        week_label=format_week_label(week_start),
        previous_week=week_start - timedelta(days=7),
        next_week=week_start + timedelta(days=7),
        shifts=sorted(shifts, key=lambda shift: (shift.shift_date, shift.start_time)),
        pending_claims=pending_claims,
        review_form=review_form,
        action_form=action_form,
    )


@schedule.route("/schedules/setup", methods=["GET", "POST"])
@login_required
def setup():
    """Manage scheduling departments and positions."""
    department_form = DepartmentForm(prefix="department")
    position_form = ShiftPositionForm(prefix="position")
    action_form = CSRFOnlyForm(prefix="setup")
    departments = Department.query.order_by(Department.name.asc()).all()
    positions = (
        ShiftPosition.query.options(selectinload(ShiftPosition.department))
        .order_by(
            ShiftPosition.department_id.asc(),
            ShiftPosition.sort_order.asc(),
            ShiftPosition.name.asc(),
        )
        .all()
    )
    users = User.query.filter(User.active.is_(True)).all()
    users = sorted(users, key=lambda user: (user.sort_key, user.email.casefold()))

    if request.method == "POST":
        action = (request.form.get("action") or "").strip()
        if action == "add_department":
            if not current_user.has_permission("schedules.manage_setup"):
                abort(403)
            if department_form.validate_on_submit():
                db.session.add(
                    Department(
                        name=(department_form.name.data or "").strip(),
                        description=(department_form.description.data or "").strip()
                        or None,
                        active=bool(department_form.active.data),
                    )
                )
                db.session.commit()
                flash("Department added.", "success")
                return redirect(url_for("schedule.setup"))
        elif action == "add_position":
            if not current_user.has_permission("schedules.manage_setup"):
                abort(403)
            if position_form.validate_on_submit():
                db.session.add(
                    ShiftPosition(
                        department_id=position_form.department_id.data,
                        name=(position_form.name.data or "").strip(),
                        description=(position_form.description.data or "").strip()
                        or None,
                        default_color=(position_form.default_color.data or "").strip()
                        or None,
                        sort_order=position_form.sort_order.data or 0,
                        active=bool(position_form.active.data),
                    )
                )
                db.session.commit()
                flash("Position added.", "success")
                return redirect(url_for("schedule.setup"))
        elif action == "toggle_department":
            if not current_user.has_permission("schedules.manage_setup"):
                abort(403)
            department = db.session.get(
                Department, _parse_int(request.form.get("department_id"))
            )
            if department is None:
                abort(404)
            department.active = not department.active
            db.session.commit()
            flash("Department updated.", "success")
            return redirect(url_for("schedule.setup"))
        elif action == "toggle_position":
            if not current_user.has_permission("schedules.manage_setup"):
                abort(403)
            position = db.session.get(
                ShiftPosition, _parse_int(request.form.get("position_id"))
            )
            if position is None:
                abort(404)
            position.active = not position.active
            db.session.commit()
            flash("Position updated.", "success")
            return redirect(url_for("schedule.setup"))

    return render_template(
        "schedules/setup.html",
        department_form=department_form,
        position_form=position_form,
        action_form=action_form,
        departments=departments,
        positions=positions,
        users=users,
        can_manage_setup=current_user.has_permission("schedules.manage_setup"),
        can_manage_pay_rates=current_user.has_permission(
            "schedules.manage_pay_rates"
        ),
    )


@schedule.route("/schedules/users/<int:user_id>", methods=["GET", "POST"])
@login_required
def user_settings(user_id: int):
    """Manage per-user scheduling settings, department memberships, and positions."""
    target_user = (
        User.query.options(
            selectinload(User.department_memberships).selectinload(
                UserDepartmentMembership.department
            ),
            selectinload(User.position_eligibilities)
            .selectinload(UserPositionEligibility.position)
            .selectinload(ShiftPosition.department),
        )
        .filter_by(id=user_id)
        .first()
    )
    if target_user is None:
        abort(404)
    if not _can_manage_user_in_any_department(current_user, target_user) and not getattr(
        current_user, "is_super_admin", False
    ):
        abort(403)

    profile_form = UserScheduleProfileForm(prefix="profile")
    membership_form = UserDepartmentMembershipForm(prefix="membership")
    eligibility_form = UserPositionEligibilityForm(prefix="eligibility")
    action_form = CSRFOnlyForm(prefix="usersettings")

    if request.method == "GET":
        profile_form.hourly_rate.data = target_user.hourly_rate
        profile_form.desired_weekly_hours.data = target_user.desired_weekly_hours
        profile_form.max_weekly_hours.data = target_user.max_weekly_hours
        profile_form.schedule_enabled.data = target_user.schedule_enabled
        profile_form.schedule_notes.data = target_user.schedule_notes or ""

    if request.method == "POST":
        action = (request.form.get("action") or "").strip()
        if action == "save_profile":
            if not current_user.has_any_permission(
                "schedules.manage_pay_rates", "schedules.manage_setup"
            ):
                abort(403)
            if profile_form.validate_on_submit():
                target_user.hourly_rate = float(profile_form.hourly_rate.data or 0.0)
                target_user.desired_weekly_hours = float(
                    profile_form.desired_weekly_hours.data or 0.0
                )
                target_user.max_weekly_hours = float(
                    profile_form.max_weekly_hours.data or 0.0
                )
                if current_user.has_permission("schedules.manage_setup"):
                    target_user.schedule_enabled = bool(
                        profile_form.schedule_enabled.data
                    )
                    target_user.schedule_notes = (
                        profile_form.schedule_notes.data or ""
                    ).strip() or None
                db.session.commit()
                flash("Scheduling settings saved.", "success")
                return redirect(url_for("schedule.user_settings", user_id=user_id))
        elif action == "add_membership":
            if not current_user.has_permission("schedules.manage_setup"):
                abort(403)
            if membership_form.validate_on_submit():
                membership_role = UserDepartmentMembership.normalize_role(
                    membership_form.role.data
                )
                existing = UserDepartmentMembership.query.filter_by(
                    user_id=target_user.id,
                    department_id=membership_form.department_id.data,
                ).first()
                if existing:
                    membership_form.department_id.errors.append(
                        "User is already in that department."
                    )
                else:
                    if membership_form.is_primary.data:
                        for membership in target_user.department_memberships:
                            membership.is_primary = False
                    db.session.add(
                        UserDepartmentMembership(
                            user=target_user,
                            department_id=membership_form.department_id.data,
                            role=membership_role,
                            can_auto_assign=bool(membership_form.can_auto_assign.data),
                            reports_to_user_id=_parse_int(
                                membership_form.reports_to_user_id.data
                            ),
                            is_primary=bool(membership_form.is_primary.data),
                        )
                    )
                    db.session.commit()
                    flash("Department membership added.", "success")
                    return redirect(url_for("schedule.user_settings", user_id=user_id))
        elif action == "update_membership_role":
            if not current_user.has_permission("schedules.manage_setup"):
                abort(403)
            membership = db.session.get(
                UserDepartmentMembership,
                _parse_int(request.form.get("membership_id")),
            )
            if membership is None or membership.user_id != target_user.id:
                abort(404)
            role_value = " ".join((request.form.get("role") or "").strip().split())
            if not role_value:
                flash("Role is required.", "danger")
                return redirect(url_for("schedule.user_settings", user_id=user_id))
            normalized_role = UserDepartmentMembership.normalize_role(role_value)
            if len(normalized_role) > 50:
                flash("Role must be 50 characters or fewer.", "danger")
                return redirect(url_for("schedule.user_settings", user_id=user_id))
            membership.role = normalized_role
            membership.can_auto_assign = _parse_checkbox(
                request.form.get("can_auto_assign")
            )
            db.session.commit()
            flash("Department membership updated.", "success")
            return redirect(url_for("schedule.user_settings", user_id=user_id))
        elif action == "remove_membership":
            if not current_user.has_permission("schedules.manage_setup"):
                abort(403)
            membership = db.session.get(
                UserDepartmentMembership,
                _parse_int(request.form.get("membership_id")),
            )
            if membership is None or membership.user_id != target_user.id:
                abort(404)
            db.session.delete(membership)
            db.session.commit()
            flash("Department membership removed.", "success")
            return redirect(url_for("schedule.user_settings", user_id=user_id))
        elif action == "add_eligibility":
            if not current_user.has_permission("schedules.manage_setup"):
                abort(403)
            if eligibility_form.validate_on_submit():
                position = db.session.get(ShiftPosition, eligibility_form.position_id.data)
                if position is None:
                    eligibility_form.position_id.errors.append("Position not found.")
                elif not any(
                    membership.department_id == position.department_id
                    for membership in target_user.department_memberships
                ):
                    eligibility_form.position_id.errors.append(
                        "Add the user to that position's department first."
                    )
                else:
                    existing = UserPositionEligibility.query.filter_by(
                        user_id=target_user.id,
                        position_id=position.id,
                    ).first()
                    if existing:
                        eligibility_form.position_id.errors.append(
                            "User already has that position."
                        )
                    else:
                        db.session.add(
                            UserPositionEligibility(
                                user=target_user,
                                position=position,
                                priority=eligibility_form.priority.data or 0,
                                active=bool(eligibility_form.active.data),
                            )
                        )
                        db.session.commit()
                        flash("Position eligibility added.", "success")
                        return redirect(url_for("schedule.user_settings", user_id=user_id))
        elif action == "remove_eligibility":
            if not current_user.has_permission("schedules.manage_setup"):
                abort(403)
            eligibility = db.session.get(
                UserPositionEligibility,
                _parse_int(request.form.get("eligibility_id")),
            )
            if eligibility is None or eligibility.user_id != target_user.id:
                abort(404)
            db.session.delete(eligibility)
            db.session.commit()
            flash("Position eligibility removed.", "success")
            return redirect(url_for("schedule.user_settings", user_id=user_id))

    return render_template(
        "schedules/user_settings.html",
        target_user=target_user,
        profile_form=profile_form,
        membership_form=membership_form,
        membership_role_suggestions=membership_form.role_suggestions,
        eligibility_form=eligibility_form,
        action_form=action_form,
        can_manage_setup=current_user.has_permission("schedules.manage_setup"),
        can_manage_pay_rates=current_user.has_permission(
            "schedules.manage_pay_rates"
        ),
    )
