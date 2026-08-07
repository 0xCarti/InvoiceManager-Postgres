from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Iterable

from flask import current_app, has_app_context
from sqlalchemy import text
from sqlalchemy.orm import selectinload

from app import db
from app.models import (
    AvailabilityOverride,
    Department,
    DepartmentScheduleWeek,
    RecurringAvailabilityWindow,
    ScheduleWeekViewReceipt,
    Setting,
    Shift,
    ShiftAudit,
    TimeOffRequest,
    TradeboardClaim,
    User,
    UserDepartmentMembership,
    UserPositionEligibility,
)
from app.utils.activity import log_activity
from app.utils.email import send_email
from app.utils.sms import send_sms
from app.utils.timezone import default_timezone_date


MATERIAL_SHIFT_FIELDS = (
    "assigned_user_id",
    "assignment_mode",
    "position_id",
    "shift_date",
    "start_time",
    "end_time",
    "paid_hours",
    "notes",
)

LEGACY_SCHEDULE_ROLE_SETTING = "SCHEDULE_MEMBERSHIP_ROLES"
LEGACY_SCHEDULE_ROLE_DEFAULTS = (
    {"name": "staff", "is_management": False},
    {"name": "manager", "is_management": True},
    {"name": "gm", "is_management": True},
)


def _normalize_legacy_schedule_role(value: object) -> str:
    return " ".join(str(value or "").strip().lower().split())


def _legacy_schedule_role_definitions() -> tuple[list[dict[str, object]], Setting | None]:
    setting = Setting.query.filter_by(name=LEGACY_SCHEDULE_ROLE_SETTING).first()
    raw_definitions: object = None
    if setting is not None and setting.value:
        try:
            raw_definitions = json.loads(setting.value)
        except (TypeError, ValueError):
            raw_definitions = None

    cleaned: list[dict[str, object]] = []
    seen_names: set[str] = set()
    if isinstance(raw_definitions, list):
        for definition in raw_definitions:
            if isinstance(definition, str):
                role_name = _normalize_legacy_schedule_role(definition)
                is_management = role_name in {"manager", "gm"}
            elif isinstance(definition, dict):
                role_name = _normalize_legacy_schedule_role(
                    definition.get("name")
                )
                is_management = bool(definition.get("is_management"))
            else:
                continue
            if not role_name or role_name in seen_names:
                continue
            cleaned.append(
                {
                    "name": role_name,
                    "is_management": is_management,
                }
            )
            seen_names.add(role_name)

    if not cleaned:
        cleaned = [dict(definition) for definition in LEGACY_SCHEDULE_ROLE_DEFAULTS]
    return cleaned, setting


def legacy_role_for_department_access(can_manage_department: bool) -> str:
    """Return a rollback role whose saved meaning matches the new access flag."""

    desired_management = bool(can_manage_department)
    definitions, setting = _legacy_schedule_role_definitions()
    definitions_by_name = {
        str(definition["name"]): bool(definition["is_management"])
        for definition in definitions
    }
    preferred_name = "manager" if desired_management else "staff"
    preferred_management = definitions_by_name.get(preferred_name)
    if preferred_management is None:
        preferred_management = preferred_name in {"manager", "gm"}
    if preferred_management == desired_management:
        return preferred_name

    for definition in definitions:
        role_name = str(definition["name"])
        if role_name == "gm":
            continue
        if bool(definition["is_management"]) == desired_management:
            return role_name

    base_name = "department manager" if desired_management else "department member"
    fallback_name = base_name
    suffix = 2
    while fallback_name in definitions_by_name:
        fallback_name = f"{base_name} {suffix}"
        suffix += 1
    definitions.append(
        {
            "name": fallback_name,
            "is_management": desired_management,
        }
    )
    if setting is None:
        setting = Setting(name=LEGACY_SCHEDULE_ROLE_SETTING)
        db.session.add(setting)
    setting.value = json.dumps(definitions, separators=(",", ":"))
    return fallback_name


@dataclass
class AutoAssignResult:
    shift_id: int
    assigned_user_id: int | None
    summary: str


SCHEDULE_WEEK_START_ADVISORY_LOCK_KEY = 0x5343484544554C45


def _valid_week_start_day(value: object, default: int = 0) -> int:
    try:
        weekday = int(value)
    except (TypeError, ValueError):
        return default
    return weekday if 0 <= weekday <= 6 else default


def get_schedule_week_start_day() -> int:
    """Return the configured Python weekday used to anchor schedule weeks."""

    if not has_app_context():
        return 0

    setting_getter = getattr(Setting, "get_schedule_week_start_day", None)
    if callable(setting_getter):
        return _valid_week_start_day(setting_getter())

    configured = current_app.config.get("SCHEDULE_WEEK_START_DAY")
    if configured not in (None, ""):
        return _valid_week_start_day(configured)

    setting_name = getattr(
        Setting,
        "SCHEDULE_WEEK_START_DAY",
        "SCHEDULE_WEEK_START_DAY",
    )
    setting = Setting.query.filter_by(name=setting_name).first()
    return _valid_week_start_day(setting.value if setting is not None else None)


def lock_schedule_week_start_setting() -> tuple[Setting, int]:
    """Serialize schedule-boundary writes and return the locked singleton.

    PostgreSQL uses a transaction-scoped advisory lock so serialization also
    works before the singleton row exists. The row lock is retained as a
    portable second layer; SQLite safely ignores ``FOR UPDATE`` and serializes
    writes using its normal database locking behavior. The caller owns the
    transaction and must commit or roll it back.
    """

    bind = db.session.get_bind()
    if bind is not None and bind.dialect.name == "postgresql":
        db.session.execute(
            text("SELECT pg_advisory_xact_lock(:lock_key)"),
            {"lock_key": SCHEDULE_WEEK_START_ADVISORY_LOCK_KEY},
        )

    setting_name = getattr(
        Setting,
        "SCHEDULE_WEEK_START_DAY",
        "SCHEDULE_WEEK_START_DAY",
    )
    setting = (
        Setting.query.filter_by(name=setting_name)
        .with_for_update()
        .first()
    )
    default_weekday = _valid_week_start_day(
        getattr(Setting, "DEFAULT_SCHEDULE_WEEK_START_DAY", 0)
    )
    if setting is None:
        setting = Setting(name=setting_name, value=str(default_weekday))
        db.session.add(setting)
        db.session.flush()
    return setting, _valid_week_start_day(setting.value, default_weekday)


def normalize_week_start(
    value: date | str | None = None,
    *,
    start_weekday: int | None = None,
) -> date:
    """Return the configured week boundary containing ``value``.

    Python weekday values are used (Monday is 0 through Sunday as 6). Passing
    ``start_weekday`` keeps data migrations and unit tests independent from the
    live setting.
    """

    if isinstance(value, str) and value.strip():
        anchor_date = datetime.strptime(value.strip(), "%Y-%m-%d").date()
    elif isinstance(value, date):
        anchor_date = value
    else:
        anchor_date = default_timezone_date() if has_app_context() else date.today()

    configured_start = (
        get_schedule_week_start_day()
        if start_weekday is None
        else _valid_week_start_day(start_weekday)
    )
    offset = (anchor_date.weekday() - configured_start) % 7
    return anchor_date - timedelta(days=offset)


def realign_schedule_weeks(new_start_weekday: int) -> dict[str, int]:
    """Re-bucket stored schedule weeks around a new global start weekday.

    The caller owns the transaction and must commit or roll it back. Published
    target weeks remain published only when every source week contributing days
    to that target was published. Mixed targets become drafts so draft shifts
    can never become visible merely because the global boundary changed.
    Existing shift audits and tradeboard claims remain attached to their shifts;
    seen receipts are reset because the logical week/version changed.
    """

    normalized_start = _valid_week_start_day(new_start_weekday, default=-1)
    if normalized_start == -1:
        raise ValueError("Schedule week start day must be between 0 and 6.")

    source_weeks = (
        DepartmentScheduleWeek.query.options(
            selectinload(DepartmentScheduleWeek.shifts),
            selectinload(DepartmentScheduleWeek.receipts),
        )
        .order_by(
            DepartmentScheduleWeek.department_id.asc(),
            DepartmentScheduleWeek.week_start.asc(),
        )
        .with_for_update()
        .all()
    )
    result = {
        "source_weeks": len(source_weeks),
        "target_weeks": 0,
        "rebuilt_weeks": 0,
        "weeks_removed": 0,
        "shifts_moved": 0,
        "moved_shifts": 0,
        "receipts_reset": 0,
        "draft_target_weeks": 0,
    }
    if not source_weeks or all(
        schedule_week.week_start.weekday() == normalized_start
        for schedule_week in source_weeks
    ):
        return result

    weeks_by_key = {
        (schedule_week.department_id, schedule_week.week_start): schedule_week
        for schedule_week in source_weeks
    }
    contributors_by_key: dict[
        tuple[int, date], dict[int, DepartmentScheduleWeek]
    ] = defaultdict(dict)
    shifts_by_key: dict[tuple[int, date], list[Shift]] = defaultdict(list)

    for source_week in source_weeks:
        for offset in range(7):
            covered_date = source_week.week_start + timedelta(days=offset)
            target_key = (
                source_week.department_id,
                normalize_week_start(
                    covered_date,
                    start_weekday=normalized_start,
                ),
            )
            contributors_by_key[target_key][source_week.id] = source_week
        for shift in list(source_week.shifts):
            target_key = (
                source_week.department_id,
                normalize_week_start(
                    shift.shift_date,
                    start_weekday=normalized_start,
                ),
            )
            contributors_by_key[target_key][source_week.id] = source_week
            shifts_by_key[target_key].append(shift)

    source_metadata = {
        schedule_week.id: {
            "is_published": bool(schedule_week.is_published),
            "current_version": int(schedule_week.current_version or 0),
            "published_at": schedule_week.published_at,
            "published_by_id": schedule_week.published_by_id,
            "updated_at": schedule_week.updated_at,
            "created_at": schedule_week.created_at,
        }
        for schedule_week in source_weeks
    }
    target_weeks: dict[tuple[int, date], DepartmentScheduleWeek] = {}
    for target_key in sorted(contributors_by_key):
        target_week = weeks_by_key.get(target_key)
        if target_week is None:
            target_week = DepartmentScheduleWeek(
                department_id=target_key[0],
                week_start=target_key[1],
            )
            db.session.add(target_week)
        target_weeks[target_key] = target_week
    db.session.flush()

    target_objects = set(target_weeks.values())
    receipts_to_reset = {
        receipt.id: receipt
        for schedule_week in source_weeks
        for receipt in list(schedule_week.receipts)
    }
    for target_week in target_objects:
        target_week.receipts.clear()
    result["receipts_reset"] = len(receipts_to_reset)

    for target_key, target_week in target_weeks.items():
        contributor_ids = list(contributors_by_key[target_key])
        contributor_metadata = [
            source_metadata[source_id] for source_id in contributor_ids
        ]
        target_shifts = shifts_by_key.get(target_key, [])
        source_versions = [
            int(metadata["current_version"] or 0)
            for metadata in contributor_metadata
        ]
        shift_versions = [int(shift.live_version or 0) for shift in target_shifts]
        target_week.current_version = max(source_versions + shift_versions + [0]) + 1
        target_week.is_published = bool(contributor_metadata) and all(
            bool(metadata["is_published"])
            for metadata in contributor_metadata
        )
        if target_week.is_published:
            published_metadata = [
                metadata
                for metadata in contributor_metadata
                if metadata["published_at"] is not None
            ]
            latest_metadata = max(
                published_metadata or contributor_metadata,
                key=lambda metadata: (
                    metadata["published_at"]
                    or metadata["updated_at"]
                    or metadata["created_at"]
                ),
            )
            target_week.published_at = latest_metadata["published_at"]
            target_week.published_by_id = latest_metadata["published_by_id"]
            target_week.unpublished_at = None
        else:
            target_week.is_published = False
            target_week.published_at = None
            target_week.published_by_id = None
            target_week.unpublished_at = datetime.utcnow()
            result["draft_target_weeks"] += 1

        for shift in target_shifts:
            if shift.schedule_week is not target_week:
                shift.schedule_week = target_week
                result["shifts_moved"] += 1
                result["moved_shifts"] += 1
            shift.live_version = target_week.current_version

    db.session.flush()

    for source_week in source_weeks:
        if source_week in target_objects:
            continue
        db.session.delete(source_week)
        result["weeks_removed"] += 1

    result["target_weeks"] = len(target_weeks)
    result["rebuilt_weeks"] = len(target_weeks)
    return result


def update_schedule_week_start_day(
    new_start_weekday: int,
) -> tuple[bool, dict[str, int]]:
    """Atomically realign schedule rows and update their global boundary.

    The schedule setting is serialized before its current value is read. Week
    rows are rebuilt while that lock is held, and the singleton value is then
    updated in the same transaction. The caller owns commit and rollback.
    """

    normalized_start = _valid_week_start_day(new_start_weekday, default=-1)
    if normalized_start == -1:
        raise ValueError("Schedule week start day must be between 0 and 6.")

    setting, current_start = lock_schedule_week_start_setting()
    if current_start == normalized_start:
        return False, {}

    result = realign_schedule_weeks(normalized_start)
    setting.value = str(normalized_start)
    return True, result


def iter_week_dates(week_start: date) -> list[date]:
    return [week_start + timedelta(days=offset) for offset in range(7)]


def format_week_label(week_start: date) -> str:
    week_end = week_start + timedelta(days=6)
    if week_start.year == week_end.year and week_start.month == week_end.month:
        return f"{week_start.strftime('%b %d')} - {week_end.strftime('%d, %Y')}"
    return f"{week_start.strftime('%b %d')} - {week_end.strftime('%b %d, %Y')}"


def get_or_create_schedule_week(
    department_id: int, week_start: date
) -> DepartmentScheduleWeek:
    _, configured_start = lock_schedule_week_start_setting()
    week_start = normalize_week_start(
        week_start,
        start_weekday=configured_start,
    )
    schedule_week = (
        DepartmentScheduleWeek.query.filter_by(
            department_id=department_id,
            week_start=week_start,
        )
        .with_for_update()
        .first()
    )
    if schedule_week is None:
        schedule_week = DepartmentScheduleWeek(
            department_id=department_id,
            week_start=week_start,
        )
        db.session.add(schedule_week)
        db.session.flush()
    return schedule_week


def get_user_membership(user: User, department_id: int) -> UserDepartmentMembership | None:
    for membership in getattr(user, "department_memberships", []):
        if membership.department_id == department_id:
            return membership
    return None


def user_department_ids(user: User) -> set[int]:
    if getattr(user, "is_super_admin", False):
        return {
            department.id
            for department in Department.query.filter_by(active=True).all()
        }
    return {
        membership.department_id
        for membership in getattr(user, "department_memberships", [])
        if membership.department and membership.department.active
    }


def user_can_view_department(user: User, department_id: int) -> bool:
    if getattr(user, "is_super_admin", False):
        return True
    if user.has_any_permission(
        "schedules.view_team",
        "schedules.edit_team",
        "schedules.publish",
        "schedules.manage_setup",
        "schedules.view_labor",
        "schedules.view_seen_status",
    ):
        membership = get_user_membership(user, department_id)
        if membership and membership.department and membership.department.active:
            return True
    if user.has_any_permission(
        "schedules.view_self",
        "schedules.self_schedule",
        "schedules.manage_self_availability",
        "schedules.view_self_time_off",
        "schedules.request_time_off",
        "schedules.view_tradeboard",
        "schedules.claim_tradeboard",
    ):
        return get_user_membership(user, department_id) is not None
    return False


def user_can_manage_department(user: User, department_id: int) -> bool:
    if getattr(user, "is_super_admin", False):
        return True
    membership = get_user_membership(user, department_id)
    if membership is None:
        return False
    return bool(membership.can_manage_department)


def user_can_auto_assign_department(user: User, department_id: int) -> bool:
    if not user.has_permission("schedules.auto_assign"):
        return False
    if getattr(user, "is_super_admin", False):
        return True
    membership = get_user_membership(user, department_id)
    if membership is None:
        return False
    return bool(membership.can_auto_assign)


def user_can_manage_other_user(
    actor: User,
    target_user: User,
    department_id: int,
) -> bool:
    if actor.id == target_user.id:
        return True
    if getattr(actor, "is_super_admin", False):
        return True
    if user_can_manage_department(actor, department_id):
        target_membership = get_user_membership(target_user, department_id)
        return target_membership is not None
    target_membership = get_user_membership(target_user, department_id)
    return bool(
        target_membership and target_membership.reports_to_user_id == actor.id
    )


def get_visible_departments(
    user: User,
    *,
    require_team_access: bool = False,
) -> list[Department]:
    department_ids = user_department_ids(user)
    query = Department.query.filter(Department.active.is_(True))
    if not getattr(user, "is_super_admin", False):
        if not department_ids:
            return []
        query = query.filter(Department.id.in_(department_ids))
    departments = query.order_by(Department.name.asc()).all()
    if not require_team_access:
        return departments
    return [
        department
        for department in departments
        if user_can_manage_department(user, department.id)
        or user.has_any_permission(
            "schedules.view_team",
            "schedules.edit_team",
            "schedules.publish",
            "schedules.view_seen_status",
            "schedules.view_labor",
            "schedules.self_schedule",
        )
    ]


def get_visible_schedule_users(
    actor: User,
    department_id: int,
    *,
    include_self_only: bool = False,
) -> list[User]:
    if include_self_only:
        return [actor]
    membership_query = (
        UserDepartmentMembership.query.options(
            selectinload(UserDepartmentMembership.user),
            selectinload(UserDepartmentMembership.department),
        )
        .filter_by(department_id=department_id)
        .join(User, UserDepartmentMembership.user_id == User.id)
        .filter(User.active.is_(True))
    )
    memberships = membership_query.all()
    users: list[User] = []
    for membership in memberships:
        if membership.user is None:
            continue
        if user_can_manage_other_user(actor, membership.user, department_id):
            users.append(membership.user)
    return sorted(users, key=lambda user: (user.sort_key, user.email.casefold()))


def calculate_paid_hours(start_time: time, end_time: time) -> float:
    duration = datetime.combine(date.today(), end_time) - datetime.combine(
        date.today(), start_time
    )
    return round(duration.total_seconds() / 3600.0, 2)


def availability_windows_for_day(user: User, weekday: int) -> list[RecurringAvailabilityWindow]:
    return [
        window
        for window in getattr(user, "recurring_availability_windows", [])
        if window.weekday == weekday
    ]


def auto_assign_hour_limit(user: User) -> float:
    """Return the effective weekly limit auto-assign should honor."""

    max_hours = float(user.max_weekly_hours or 0.0)
    if max_hours > 0:
        return max_hours
    desired_hours = float(user.desired_weekly_hours or 0.0)
    if desired_hours > 0:
        return desired_hours
    return 0.0


def time_off_overlaps(
    request_obj: TimeOffRequest,
    shift_date: date,
    start_time: time,
    end_time: time,
) -> bool:
    if request_obj.status != TimeOffRequest.STATUS_APPROVED:
        return False
    if shift_date < request_obj.start_date or shift_date > request_obj.end_date:
        return False
    if request_obj.is_full_day:
        return True
    request_start = request_obj.start_time or time.min
    request_end = request_obj.end_time or time.max
    return not (end_time <= request_start or start_time >= request_end)


def override_blocks_shift(
    override_obj: AvailabilityOverride,
    shift_date: date,
    start_time: time,
    end_time: time,
) -> bool:
    shift_start = datetime.combine(shift_date, start_time)
    shift_end = datetime.combine(shift_date, end_time)
    overlaps = not (
        override_obj.end_at <= shift_start or override_obj.start_at >= shift_end
    )
    if not overlaps:
        return False
    return not override_obj.is_available


def override_allows_shift(
    override_obj: AvailabilityOverride,
    shift_date: date,
    start_time: time,
    end_time: time,
) -> bool:
    shift_start = datetime.combine(shift_date, start_time)
    shift_end = datetime.combine(shift_date, end_time)
    return (
        override_obj.is_available
        and override_obj.start_at <= shift_start
        and override_obj.end_at >= shift_end
    )


def user_is_available_for_shift(
    user: User,
    shift_date: date,
    start_time: time,
    end_time: time,
) -> bool:
    for request_obj in getattr(user, "time_off_requests", []):
        if time_off_overlaps(request_obj, shift_date, start_time, end_time):
            return False

    overrides = list(getattr(user, "availability_overrides", []))
    if any(
        override_blocks_shift(override_obj, shift_date, start_time, end_time)
        for override_obj in overrides
    ):
        return False
    if any(
        override_allows_shift(override_obj, shift_date, start_time, end_time)
        for override_obj in overrides
    ):
        return True

    all_windows = list(getattr(user, "recurring_availability_windows", []))
    if not all_windows:
        return True

    windows = [
        window for window in all_windows if window.weekday == shift_date.weekday()
    ]
    if not windows:
        return False
    return any(
        window.start_time <= start_time and window.end_time >= end_time
        for window in windows
    )


def find_overlapping_shift(
    user_id: int,
    shift_date: date,
    start_time: time,
    end_time: time,
    *,
    exclude_shift_id: int | None = None,
) -> Shift | None:
    query = Shift.query.filter(
        Shift.assigned_user_id == user_id,
        Shift.shift_date == shift_date,
        Shift.start_time < end_time,
        Shift.end_time > start_time,
    )
    if exclude_shift_id is not None:
        query = query.filter(Shift.id != exclude_shift_id)
    return query.first()


def assigned_hours_for_week(
    user_id: int,
    schedule_week_id: int,
    *,
    exclude_shift_id: int | None = None,
) -> float:
    query = Shift.query.filter(
        Shift.assigned_user_id == user_id,
        Shift.schedule_week_id == schedule_week_id,
    )
    if exclude_shift_id is not None:
        query = query.filter(Shift.id != exclude_shift_id)
    return float(
        sum((shift.paid_hours or 0.0) for shift in query.all())
    )


def capture_shift_snapshot(shift: Shift | None) -> dict | None:
    if shift is None:
        return None
    return {
        "id": shift.id,
        "assigned_user_id": shift.assigned_user_id,
        "assignment_mode": shift.assignment_mode,
        "position_id": shift.position_id,
        "shift_date": shift.shift_date.isoformat() if shift.shift_date else None,
        "start_time": shift.start_time.isoformat() if shift.start_time else None,
        "end_time": shift.end_time.isoformat() if shift.end_time else None,
        "paid_hours": float(shift.paid_hours or 0.0),
        "notes": shift.notes or "",
        "color": shift.color or "",
    }


def material_change_fields(before: dict | None, after: dict | None) -> list[str]:
    if before is None or after is None:
        return list(MATERIAL_SHIFT_FIELDS)
    changed = []
    for field in MATERIAL_SHIFT_FIELDS:
        if before.get(field) != after.get(field):
            changed.append(field)
    return changed


def apply_rate_snapshot(shift: Shift) -> None:
    shift.hourly_rate_snapshot = float(
        (shift.assigned_user.hourly_rate or 0.0) if shift.assigned_user else 0.0
    )


def record_shift_audit(
    shift: Shift,
    *,
    actor: User | None,
    action: str,
    version: int,
    before: dict | None,
    after: dict | None,
    summary: str,
) -> None:
    db.session.add(
        ShiftAudit(
            shift=shift,
            action=action,
            version=version,
            summary=summary,
            details={"before": before, "after": after},
            changed_by=actor,
        )
    )


def mark_schedule_week_seen(
    user: User,
    schedule_weeks: Iterable[DepartmentScheduleWeek],
) -> None:
    now = datetime.utcnow()
    for schedule_week in schedule_weeks:
        receipt = ScheduleWeekViewReceipt.query.filter_by(
            schedule_week_id=schedule_week.id,
            user_id=user.id,
        ).first()
        if receipt is None:
            receipt = ScheduleWeekViewReceipt(
                schedule_week=schedule_week,
                user=user,
                first_seen_at=now,
            )
            db.session.add(receipt)
        if receipt.first_seen_at is None:
            receipt.first_seen_at = now
        receipt.last_seen_at = now
        receipt.last_seen_version = schedule_week.current_version or 0


def approved_tradeboard_claim(shift: Shift) -> TradeboardClaim | None:
    for claim in shift.tradeboard_claims:
        if claim.status == TradeboardClaim.STATUS_APPROVED:
            return claim
    return None


def build_auto_assign_candidates(
    shift: Shift,
) -> list[tuple[User, UserPositionEligibility]]:
    department_id = shift.schedule_week.department_id
    eligibilities = (
        UserPositionEligibility.query.options(
            selectinload(UserPositionEligibility.user)
            .selectinload(User.department_memberships),
            selectinload(UserPositionEligibility.user)
            .selectinload(User.recurring_availability_windows),
            selectinload(UserPositionEligibility.user)
            .selectinload(User.availability_overrides),
            selectinload(UserPositionEligibility.user)
            .selectinload(User.time_off_requests),
        )
        .filter_by(position_id=shift.position_id, active=True)
        .all()
    )
    candidates: list[tuple[User, UserPositionEligibility]] = []
    for eligibility in eligibilities:
        user = eligibility.user
        if user is None or not user.active or not user.schedule_enabled:
            continue
        if get_user_membership(user, department_id) is None:
            continue
        candidates.append((user, eligibility))
    return candidates


def auto_assign_shifts(
    schedule_week: DepartmentScheduleWeek,
    *,
    actor: User | None,
    shift_ids: Iterable[int] | None = None,
) -> list[AutoAssignResult]:
    if schedule_week.is_published:
        return [
            AutoAssignResult(
                shift_id=0,
                assigned_user_id=None,
                summary="Published weeks cannot be auto-assigned.",
            )
        ]
    query = Shift.query.options(
        selectinload(Shift.schedule_week),
        selectinload(Shift.position),
    ).filter(
        Shift.schedule_week_id == schedule_week.id,
        Shift.assigned_user_id.is_(None),
        Shift.assignment_mode == Shift.ASSIGNMENT_OPEN,
        Shift.is_locked.is_(False),
    )
    if shift_ids:
        query = query.filter(Shift.id.in_(list(shift_ids)))
    shifts = query.order_by(Shift.shift_date.asc(), Shift.start_time.asc()).all()
    results: list[AutoAssignResult] = []
    for shift in shifts:
        best_choice: tuple | None = None
        had_candidates = False
        blocked_by_availability = False
        blocked_by_overlap = False
        blocked_by_hours = False
        for user, eligibility in build_auto_assign_candidates(shift):
            had_candidates = True
            if not user_is_available_for_shift(
                user, shift.shift_date, shift.start_time, shift.end_time
            ):
                blocked_by_availability = True
                continue
            if find_overlapping_shift(
                user.id,
                shift.shift_date,
                shift.start_time,
                shift.end_time,
                exclude_shift_id=shift.id,
            ):
                blocked_by_overlap = True
                continue
            assigned_hours = assigned_hours_for_week(
                user.id,
                schedule_week.id,
                exclude_shift_id=shift.id,
            )
            hour_limit = auto_assign_hour_limit(user)
            if hour_limit and assigned_hours + float(shift.paid_hours or 0.0) > hour_limit:
                blocked_by_hours = True
                continue
            desired_hours = float(user.desired_weekly_hours or 0.0)
            desired_gap = max(desired_hours - assigned_hours, 0.0)
            score = (
                int(eligibility.priority or 0),
                desired_gap,
                -(assigned_hours),
                -(user.id or 0),
            )
            if best_choice is None or score > best_choice[0]:
                best_choice = (score, user, assigned_hours, desired_gap, eligibility)

        if best_choice is None:
            summary = "No eligible user matched availability and hours."
            if not had_candidates:
                summary = "No eligible users are configured for this position."
            elif blocked_by_hours and not blocked_by_availability and not blocked_by_overlap:
                summary = "Eligible users would exceed their preferred/max weekly hours."
            elif blocked_by_availability and not blocked_by_overlap and not blocked_by_hours:
                summary = "Eligible users exist, but none are available for this shift."
            elif blocked_by_overlap and not blocked_by_availability and not blocked_by_hours:
                summary = "Eligible users already have overlapping shifts."
            results.append(
                AutoAssignResult(
                    shift_id=shift.id,
                    assigned_user_id=None,
                    summary=summary,
                )
            )
            continue

        _score, user, assigned_hours, desired_gap, eligibility = best_choice
        before = capture_shift_snapshot(shift)
        shift.assigned_user = user
        shift.assignment_mode = Shift.ASSIGNMENT_ASSIGNED
        apply_rate_snapshot(shift)
        if schedule_week.is_published:
            schedule_week.current_version += 1
            shift.live_version = schedule_week.current_version
        record_shift_audit(
            shift,
            actor=actor,
            action="auto_assigned",
            version=schedule_week.current_version,
            before=before,
            after=capture_shift_snapshot(shift),
            summary=(
                f"Auto-assigned to {user.email} "
                f"(priority {eligibility.priority}, "
                f"assigned hours {assigned_hours:.2f}, "
                f"desired gap {desired_gap:.2f})."
            ),
        )
        results.append(
            AutoAssignResult(
                shift_id=shift.id,
                assigned_user_id=user.id,
                summary=f"Assigned to {user.email}.",
            )
        )
    return results


def scoped_time_off_approvers(request_user: User) -> list[User]:
    query = User.query.options(
        selectinload(User.department_memberships),
    ).filter(User.active.is_(True))
    approvers: list[User] = []
    request_department_ids = {
        membership.department_id for membership in request_user.department_memberships
    }
    for candidate in query.all():
        if not candidate.has_permission("schedules.approve_time_off"):
            continue
        if getattr(candidate, "is_super_admin", False):
            approvers.append(candidate)
            continue
        if any(
            user_can_manage_other_user(candidate, request_user, department_id)
            for department_id in request_department_ids
        ):
            approvers.append(candidate)
    return approvers


def shift_display_line(shift: Shift) -> str:
    return (
        f"{shift.shift_date.strftime('%a %b %d')} "
        f"{shift.start_time.strftime('%I:%M%p').lstrip('0')} - "
        f"{shift.end_time.strftime('%I:%M%p').lstrip('0')} "
        f"({shift.position.name})"
    )


def _safe_send_email(to_address: str, subject: str, body: str) -> None:
    try:
        send_email(to_address, subject, body)
    except Exception as exc:  # pragma: no cover - defensive logging
        current_app.logger.warning("Schedule email failed for %s: %s", to_address, exc)


def _safe_send_sms(to_number: str, body: str) -> None:
    try:
        send_sms(to_number, body)
    except Exception as exc:  # pragma: no cover - defensive logging
        current_app.logger.warning("Schedule SMS failed for %s: %s", to_number, exc)


def _deliver_user_notification(
    user: User,
    *,
    email_enabled: bool,
    text_enabled: bool,
    subject: str,
    body: str,
) -> None:
    if email_enabled:
        _safe_send_email(user.email, subject, body)
    if text_enabled and user.phone_number:
        _safe_send_sms(user.phone_number, body[:320])


def notify_schedule_posted(
    schedule_week: DepartmentScheduleWeek,
    shifts: Iterable[Shift],
) -> None:
    week_label = format_week_label(schedule_week.week_start)
    assigned_lines: dict[int, list[str]] = defaultdict(list)
    tradeboard_lines: dict[int, list[str]] = defaultdict(list)
    eligible_by_position: dict[int, list[User]] = defaultdict(list)
    eligibilities = (
        UserPositionEligibility.query.options(
            selectinload(UserPositionEligibility.user)
        )
        .filter(
            UserPositionEligibility.position_id.in_(
                {shift.position_id for shift in shifts if shift.position_id}
            ),
            UserPositionEligibility.active.is_(True),
        )
        .all()
    )
    for eligibility in eligibilities:
        if eligibility.user and eligibility.user.active and eligibility.user.schedule_enabled:
            eligible_by_position[eligibility.position_id].append(eligibility.user)

    for shift in shifts:
        line = shift_display_line(shift)
        if shift.assigned_user_id and shift.assigned_user:
            assigned_lines[shift.assigned_user_id].append(line)
        if shift.assignment_mode in {Shift.ASSIGNMENT_OPEN, Shift.ASSIGNMENT_TRADEBOARD}:
            for user in eligible_by_position.get(shift.position_id, []):
                tradeboard_lines[user.id].append(line)

    for user_id, lines in assigned_lines.items():
        user = db.session.get(User, user_id)
        if user is None:
            continue
        body = "Your schedule has been posted for the week of " + week_label + ":\n\n"
        body += "\n".join(f"- {line}" for line in lines)
        _deliver_user_notification(
            user,
            email_enabled=user.notify_schedule_post_email,
            text_enabled=user.notify_schedule_post_text,
            subject=f"Schedule posted: {week_label}",
            body=body,
        )

    for user_id, lines in tradeboard_lines.items():
        user = db.session.get(User, user_id)
        if user is None:
            continue
        body = "New open/tradeboard shifts are available for the week of "
        body += week_label + ":\n\n"
        body += "\n".join(f"- {line}" for line in sorted(set(lines)))
        _deliver_user_notification(
            user,
            email_enabled=user.notify_tradeboard_email,
            text_enabled=user.notify_tradeboard_text,
            subject=f"Tradeboard shifts available: {week_label}",
            body=body,
        )


def notify_schedule_changes(
    schedule_week: DepartmentScheduleWeek,
    change_records: list[tuple[dict | None, Shift]],
) -> None:
    week_label = format_week_label(schedule_week.week_start)
    assigned_change_lines: dict[int, list[str]] = defaultdict(list)
    tradeboard_change_lines: dict[int, list[str]] = defaultdict(list)
    position_ids = {shift.position_id for _before, shift in change_records if shift.position_id}
    eligible_by_position: dict[int, list[User]] = defaultdict(list)
    if position_ids:
        eligibilities = (
            UserPositionEligibility.query.options(
                selectinload(UserPositionEligibility.user)
            )
            .filter(
                UserPositionEligibility.position_id.in_(position_ids),
                UserPositionEligibility.active.is_(True),
            )
            .all()
        )
        for eligibility in eligibilities:
            if eligibility.user and eligibility.user.active and eligibility.user.schedule_enabled:
                eligible_by_position[eligibility.position_id].append(eligibility.user)

    for before, shift in change_records:
        after = capture_shift_snapshot(shift)
        changed_fields = material_change_fields(before, after)
        if not changed_fields:
            continue
        line = shift_display_line(shift)
        old_assigned_user_id = before.get("assigned_user_id") if before else None
        if old_assigned_user_id and old_assigned_user_id != shift.assigned_user_id:
            assigned_change_lines[old_assigned_user_id].append(
                f"Removed/changed: {line}"
            )
        if shift.assigned_user_id:
            assigned_change_lines[shift.assigned_user_id].append(
                f"Updated: {line}"
            )
        if shift.assignment_mode in {Shift.ASSIGNMENT_OPEN, Shift.ASSIGNMENT_TRADEBOARD}:
            for user in eligible_by_position.get(shift.position_id, []):
                tradeboard_change_lines[user.id].append(f"Updated: {line}")

    for user_id, lines in assigned_change_lines.items():
        user = db.session.get(User, user_id)
        if user is None:
            continue
        body = "Your published schedule changed for the week of "
        body += week_label + ":\n\n"
        body += "\n".join(f"- {line}" for line in sorted(set(lines)))
        _deliver_user_notification(
            user,
            email_enabled=user.notify_schedule_changes_email,
            text_enabled=user.notify_schedule_changes_text,
            subject=f"Schedule updated: {week_label}",
            body=body,
        )

    for user_id, lines in tradeboard_change_lines.items():
        user = db.session.get(User, user_id)
        if user is None:
            continue
        body = "Tradeboard/open shifts changed for the week of "
        body += week_label + ":\n\n"
        body += "\n".join(f"- {line}" for line in sorted(set(lines)))
        _deliver_user_notification(
            user,
            email_enabled=user.notify_tradeboard_email,
            text_enabled=user.notify_tradeboard_text,
            subject=f"Tradeboard updated: {week_label}",
            body=body,
        )


def notify_time_off_submitted(request_obj: TimeOffRequest) -> None:
    managers = scoped_time_off_approvers(request_obj.user)
    body = (
        f"{request_obj.user.email} submitted a time-off request "
        f"from {request_obj.start_date} to {request_obj.end_date}.\n\n"
        f"Reason:\n{request_obj.reason}"
    )
    for manager in managers:
        _deliver_user_notification(
            manager,
            email_enabled=True,
            text_enabled=False,
            subject="Time-off request submitted",
            body=body,
        )


def notify_time_off_reviewed(request_obj: TimeOffRequest) -> None:
    body = (
        f"Your time-off request from {request_obj.start_date} "
        f"to {request_obj.end_date} was {request_obj.status}."
    )
    if request_obj.manager_note:
        body += f"\n\nManager note:\n{request_obj.manager_note}"
    _deliver_user_notification(
        request_obj.user,
        email_enabled=request_obj.user.notify_schedule_changes_email,
        text_enabled=request_obj.user.notify_schedule_changes_text,
        subject="Time-off request updated",
        body=body,
    )


def log_schedule_action(message: str) -> None:
    log_activity(message)
