from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Iterable

from flask import current_app
from sqlalchemy.orm import selectinload

from app import db
from app.models import (
    AvailabilityOverride,
    Department,
    DepartmentScheduleWeek,
    RecurringAvailabilityWindow,
    ScheduleWeekViewReceipt,
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


@dataclass
class AutoAssignResult:
    shift_id: int
    assigned_user_id: int | None
    summary: str


def normalize_week_start(value: date | str | None = None) -> date:
    if isinstance(value, str) and value.strip():
        parsed = datetime.strptime(value.strip(), "%Y-%m-%d").date()
        return parsed - timedelta(days=parsed.weekday())
    if isinstance(value, date):
        return value - timedelta(days=value.weekday())
    today = date.today()
    return today - timedelta(days=today.weekday())


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
    week_start = normalize_week_start(week_start)
    schedule_week = DepartmentScheduleWeek.query.filter_by(
        department_id=department_id,
        week_start=week_start,
    ).first()
    if schedule_week is None:
        schedule_week = DepartmentScheduleWeek(
            department_id=department_id,
            week_start=week_start,
        )
        db.session.add(schedule_week)
        db.session.flush()
    return schedule_week


def user_is_schedule_gm(user: User) -> bool:
    if getattr(user, "is_super_admin", False):
        return True
    return any(
        UserDepartmentMembership.is_gm_role(membership.role)
        for membership in getattr(user, "department_memberships", [])
    )


def get_user_membership(user: User, department_id: int) -> UserDepartmentMembership | None:
    for membership in getattr(user, "department_memberships", []):
        if membership.department_id == department_id:
            return membership
    return None


def user_department_ids(user: User) -> set[int]:
    if getattr(user, "is_super_admin", False) or user_is_schedule_gm(user):
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
    if getattr(user, "is_super_admin", False) or user_is_schedule_gm(user):
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
    if getattr(user, "is_super_admin", False) or user_is_schedule_gm(user):
        return True
    membership = get_user_membership(user, department_id)
    if membership is None:
        return False
    return UserDepartmentMembership.is_management_role(membership.role)


def user_can_auto_assign_department(user: User, department_id: int) -> bool:
    if not user.has_permission("schedules.auto_assign"):
        return False
    if getattr(user, "is_super_admin", False) or user_is_schedule_gm(user):
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
    if getattr(actor, "is_super_admin", False) or user_is_schedule_gm(actor):
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
    if not (getattr(user, "is_super_admin", False) or user_is_schedule_gm(user)):
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
        .order_by(User.email.asc())
    )
    memberships = membership_query.all()
    users: list[User] = []
    for membership in memberships:
        if membership.user is None:
            continue
        if user_can_manage_other_user(actor, membership.user, department_id):
            users.append(membership.user)
    return users


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
        if getattr(candidate, "is_super_admin", False) or user_is_schedule_gm(candidate):
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
        elif shift.assignment_mode in {Shift.ASSIGNMENT_OPEN, Shift.ASSIGNMENT_TRADEBOARD}:
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
        elif shift.assignment_mode in {Shift.ASSIGNMENT_OPEN, Shift.ASSIGNMENT_TRADEBOARD}:
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
