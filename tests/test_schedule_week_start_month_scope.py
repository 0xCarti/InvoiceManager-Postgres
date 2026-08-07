from datetime import date, datetime, time

from sqlalchemy import event
from werkzeug.security import generate_password_hash

from app import db
from app.models import (
    Department,
    DepartmentScheduleWeek,
    ScheduleTemplate,
    ScheduleWeekViewReceipt,
    Shift,
    ShiftAudit,
    ShiftPosition,
    TradeboardClaim,
    User,
    UserDepartmentMembership,
    UserPositionEligibility,
)
from app.routes import schedule_routes
from app.services import schedule_service
from app.services.schedule_service import (
    get_or_create_schedule_week,
    normalize_week_start,
    realign_schedule_weeks,
    update_schedule_week_start_day,
)
from tests.permission_helpers import grant_permissions
from tests.utils import login


def _create_user(email: str, password: str = "pass") -> User:
    user = User(
        email=email,
        password=generate_password_hash(password),
        active=True,
    )
    db.session.add(user)
    db.session.flush()
    return user


def test_configured_week_start_normalizes_dates_and_rotates_offset_labels(
    app,
    monkeypatch,
):
    with app.app_context():
        from app.models import Setting

        Setting.set_schedule_week_start_day(2)
        db.session.commit()
        monkeypatch.setattr(
            schedule_service,
            "default_timezone_date",
            lambda: date(2026, 8, 4),
        )

        assert normalize_week_start(date(2026, 8, 5)) == date(2026, 8, 5)
        assert normalize_week_start(date(2026, 8, 11)) == date(2026, 8, 5)
        assert normalize_week_start(date(2026, 8, 4)) == date(2026, 7, 29)
        assert normalize_week_start() == date(2026, 7, 29)
        assert schedule_routes._schedule_weekday_offset_choices(
            date(2026, 8, 5)
        ) == [
            (0, "Wed"),
            (1, "Thu"),
            (2, "Fri"),
            (3, "Sat"),
            (4, "Sun"),
            (5, "Mon"),
            (6, "Tue"),
        ]


def test_week_template_weekday_remains_an_absolute_weekday(app):
    with app.app_context():
        from app.models import Setting

        Setting.set_schedule_week_start_day(2)
        db.session.commit()
        template = ScheduleTemplate(span=ScheduleTemplate.SPAN_WEEK)
        monday_entry = type("Entry", (), {"weekday": 0})()

        assert schedule_routes._expand_template_entry_date(
            template,
            monday_entry,
            date(2026, 8, 5),
        ) == date(2026, 8, 10)


def test_realign_schedule_weeks_rebuckets_transactionally_and_preserves_shift_history(
    app,
):
    with app.app_context():
        worker = _create_user("week-realign-worker@example.com")
        department = Department(name="Week Realignment", active=True)
        db.session.add(department)
        db.session.flush()
        position = ShiftPosition(
            department_id=department.id,
            name="Week Realignment Position",
            active=True,
        )
        db.session.add(position)
        db.session.flush()

        first_week = DepartmentScheduleWeek(
            department_id=department.id,
            week_start=date(2026, 6, 1),
            is_published=True,
            current_version=3,
            published_at=datetime(2026, 5, 29, 12, 0),
        )
        second_week = DepartmentScheduleWeek(
            department_id=department.id,
            week_start=date(2026, 6, 8),
            is_published=False,
            current_version=4,
        )
        db.session.add_all([first_week, second_week])
        db.session.flush()

        first_shift = Shift(
            schedule_week_id=first_week.id,
            position_id=position.id,
            assigned_user_id=worker.id,
            shift_date=date(2026, 6, 2),
            start_time=time(9, 0),
            end_time=time(17, 0),
            paid_hours=8,
            assignment_mode=Shift.ASSIGNMENT_ASSIGNED,
            live_version=3,
        )
        second_shift = Shift(
            schedule_week_id=second_week.id,
            position_id=position.id,
            shift_date=date(2026, 6, 8),
            start_time=time(10, 0),
            end_time=time(14, 0),
            paid_hours=4,
            assignment_mode=Shift.ASSIGNMENT_TRADEBOARD,
            live_version=4,
        )
        db.session.add_all([first_shift, second_shift])
        db.session.flush()
        db.session.add_all(
            [
                ShiftAudit(
                    shift_id=first_shift.id,
                    action="created",
                    version=3,
                ),
                TradeboardClaim(
                    shift_id=second_shift.id,
                    user_id=worker.id,
                    status=TradeboardClaim.STATUS_PENDING,
                ),
                ScheduleWeekViewReceipt(
                    schedule_week_id=first_week.id,
                    user_id=worker.id,
                    last_seen_version=3,
                ),
            ]
        )
        db.session.commit()
        first_week_id = first_week.id
        second_week_id = second_week.id
        first_shift_id = first_shift.id
        second_shift_id = second_shift.id

        result = realign_schedule_weeks(2)

        assert result["source_weeks"] == 2
        assert result["target_weeks"] == result["rebuilt_weeks"] == 3
        assert result["shifts_moved"] == result["moved_shifts"] == 2
        assert result["weeks_removed"] == 2
        assert result["receipts_reset"] == 1
        assert result["draft_target_weeks"] == 2

        db.session.rollback()
        assert (
            db.session.get(DepartmentScheduleWeek, first_week_id) is not None
        )
        assert (
            db.session.get(DepartmentScheduleWeek, second_week_id) is not None
        )
        assert ScheduleWeekViewReceipt.query.count() == 1

        committed_result = realign_schedule_weeks(2)
        db.session.commit()

        assert committed_result["moved_shifts"] == 2
        assert {
            week.week_start for week in DepartmentScheduleWeek.query.all()
        } == {
            date(2026, 5, 27),
            date(2026, 6, 3),
            date(2026, 6, 10),
        }
        assert db.session.get(DepartmentScheduleWeek, first_week_id) is None
        assert db.session.get(DepartmentScheduleWeek, second_week_id) is None
        assert db.session.get(
            Shift, first_shift_id
        ).schedule_week.week_start == date(2026, 5, 27)
        assert db.session.get(
            Shift, second_shift_id
        ).schedule_week.week_start == date(2026, 6, 3)
        assert ShiftAudit.query.filter_by(shift_id=first_shift_id).count() == 1
        assert (
            TradeboardClaim.query.filter_by(shift_id=second_shift_id).count()
            == 1
        )
        assert ScheduleWeekViewReceipt.query.count() == 0
        assert all(
            week.week_start.weekday() == 2
            for week in DepartmentScheduleWeek.query.all()
        )


def test_week_start_update_locks_setting_before_realigning_rows(app):
    with app.app_context():
        from app.models import Setting

        Setting.set_schedule_week_start_day(0)
        department = Department(name="Serialized Week Update", active=True)
        db.session.add(department)
        db.session.flush()
        original_week = DepartmentScheduleWeek(
            department_id=department.id,
            week_start=date(2026, 8, 3),
        )
        db.session.add(original_week)
        db.session.commit()
        original_week_id = original_week.id

        statements = []

        def capture_statement(
            _connection,
            _cursor,
            statement,
            _parameters,
            _context,
            _executemany,
        ):
            statements.append(" ".join(statement.lower().split()))

        event.listen(db.engine, "before_cursor_execute", capture_statement)
        try:
            changed, _result = update_schedule_week_start_day(2)
        finally:
            event.remove(db.engine, "before_cursor_execute", capture_statement)

        assert changed is True
        requires_for_update = db.engine.dialect.name != "sqlite"
        setting_select_index = next(
            index
            for index, statement in enumerate(statements)
            if "from setting" in statement
            and (not requires_for_update or "for update" in statement)
        )
        week_select_index = next(
            index
            for index, statement in enumerate(statements)
            if "from schedule_department_week" in statement
            and (not requires_for_update or "for update" in statement)
        )
        if db.engine.dialect.name == "postgresql":
            advisory_index = next(
                index
                for index, statement in enumerate(statements)
                if "pg_advisory_xact_lock" in statement
            )
            assert advisory_index < setting_select_index < week_select_index
        else:
            assert not any(
                "pg_advisory_xact_lock" in statement
                for statement in statements
            )
            assert setting_select_index < week_select_index

        assert Setting.get_schedule_week_start_day() == 2
        db.session.rollback()
        assert Setting.get_schedule_week_start_day() == 0
        assert (
            db.session.get(DepartmentScheduleWeek, original_week_id)
            is not None
        )


def test_get_or_create_week_uses_same_serialization_lock(app):
    with app.app_context():
        from app.models import Setting

        Setting.set_schedule_week_start_day(2)
        department = Department(name="Serialized Week Creation", active=True)
        db.session.add(department)
        db.session.commit()
        department_id = department.id

        statements = []

        def capture_statement(
            _connection,
            _cursor,
            statement,
            _parameters,
            _context,
            _executemany,
        ):
            statements.append(" ".join(statement.lower().split()))

        event.listen(db.engine, "before_cursor_execute", capture_statement)
        try:
            schedule_week = get_or_create_schedule_week(
                department_id,
                date(2026, 8, 7),
            )
        finally:
            event.remove(db.engine, "before_cursor_execute", capture_statement)

        assert schedule_week.week_start == date(2026, 8, 5)
        requires_for_update = db.engine.dialect.name != "sqlite"
        setting_select_index = next(
            index
            for index, statement in enumerate(statements)
            if "from setting" in statement
            and (not requires_for_update or "for update" in statement)
        )
        week_select_index = next(
            index
            for index, statement in enumerate(statements)
            if "from schedule_department_week" in statement
            and (not requires_for_update or "for update" in statement)
        )
        if db.engine.dialect.name == "postgresql":
            advisory_index = next(
                index
                for index, statement in enumerate(statements)
                if "pg_advisory_xact_lock" in statement
            )
            assert advisory_index < setting_select_index < week_select_index
        else:
            assert not any(
                "pg_advisory_xact_lock" in statement
                for statement in statements
            )
            assert setting_select_index < week_select_index

        db.session.rollback()
        assert (
            DepartmentScheduleWeek.query.filter_by(
                department_id=department_id,
                week_start=date(2026, 8, 5),
            ).first()
            is None
        )


def test_team_schedule_month_uses_configured_calendar_and_is_read_only(
    client,
    app,
    monkeypatch,
):
    with app.app_context():
        from app.models import Setting

        Setting.set_schedule_week_start_day(2)
        viewer = _create_user("month-schedule-viewer@example.com")
        department = Department(
            name="Monthly Schedule Department", active=True
        )
        db.session.add(department)
        db.session.flush()
        position = ShiftPosition(
            department_id=department.id,
            name="Monthly Schedule Position",
            active=True,
        )
        db.session.add(position)
        db.session.flush()
        db.session.add(
            UserDepartmentMembership(
                user_id=viewer.id,
                department_id=department.id,
                is_primary=True,
            )
        )
        schedule_week = DepartmentScheduleWeek(
            department_id=department.id,
            week_start=date(2026, 7, 29),
            is_published=True,
            current_version=1,
        )
        db.session.add(schedule_week)
        db.session.flush()
        db.session.add(
            Shift(
                schedule_week_id=schedule_week.id,
                position_id=position.id,
                assigned_user_id=viewer.id,
                shift_date=date(2026, 8, 1),
                start_time=time(9, 0),
                end_time=time(17, 0),
                paid_hours=8,
                hourly_rate_snapshot=25,
                assignment_mode=Shift.ASSIGNMENT_ASSIGNED,
                live_version=1,
            )
        )
        db.session.commit()
        grant_permissions(
            viewer,
            "schedules.view_team",
            group_name="Monthly Schedule View",
            description="View the read-only monthly team schedule.",
        )
        department_id = department.id

    configured_weekday_reads = []

    def load_configured_weekday_once():
        configured_weekday_reads.append(True)
        return 2

    def reject_implicit_weekday_load():
        raise AssertionError(
            "Month calendar normalization must reuse the request's weekday value."
        )

    monkeypatch.setattr(
        schedule_routes,
        "get_schedule_week_start_day",
        load_configured_weekday_once,
    )
    monkeypatch.setattr(
        schedule_service,
        "get_schedule_week_start_day",
        reject_implicit_weekday_load,
    )

    with client:
        login(client, "month-schedule-viewer@example.com", "pass")
        response = client.get(
            f"/schedules/month?department_id={department_id}&month=2026-08-15"
        )
        post_response = client.post(
            f"/schedules/month?department_id={department_id}&month=2026-08-15"
        )

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "August 2026" in body
    assert "Read-only monthly overview" in body
    assert "Monthly Schedule Position" in body
    assert body.index(">Wednesday<") < body.index(">Thursday<")
    assert "Scheduled Labor" not in body
    assert 'name="action"' not in body
    assert post_response.status_code == 405
    assert len(configured_weekday_reads) == 1


def test_team_schedule_month_self_scheduler_cannot_see_coworker_shifts(
    client,
    app,
    monkeypatch,
):
    with app.app_context():
        from app.models import Setting

        Setting.set_schedule_week_start_day(2)
        self_scheduler = _create_user("month-self-only@example.com")
        coworker = _create_user("month-hidden-coworker@example.com")
        department = Department(name="Month Self Scope", active=True)
        db.session.add(department)
        db.session.flush()
        position = ShiftPosition(
            department_id=department.id,
            name="Month Self Position",
            active=True,
        )
        db.session.add(position)
        db.session.flush()
        db.session.add_all(
            [
                UserDepartmentMembership(
                    user_id=self_scheduler.id,
                    department_id=department.id,
                    is_primary=True,
                ),
                UserDepartmentMembership(
                    user_id=coworker.id,
                    department_id=department.id,
                    is_primary=True,
                ),
            ]
        )
        schedule_week = DepartmentScheduleWeek(
            department_id=department.id,
            week_start=date(2026, 8, 5),
            is_published=True,
            current_version=1,
        )
        db.session.add(schedule_week)
        db.session.flush()
        db.session.add_all(
            [
                Shift(
                    schedule_week_id=schedule_week.id,
                    position_id=position.id,
                    assigned_user_id=self_scheduler.id,
                    shift_date=date(2026, 8, 6),
                    start_time=time(9, 0),
                    end_time=time(13, 0),
                    paid_hours=4,
                    assignment_mode=Shift.ASSIGNMENT_ASSIGNED,
                    live_version=1,
                ),
                Shift(
                    schedule_week_id=schedule_week.id,
                    position_id=position.id,
                    assigned_user_id=coworker.id,
                    shift_date=date(2026, 8, 7),
                    start_time=time(13, 0),
                    end_time=time(17, 0),
                    paid_hours=4,
                    assignment_mode=Shift.ASSIGNMENT_ASSIGNED,
                    live_version=1,
                ),
            ]
        )
        db.session.commit()
        grant_permissions(
            self_scheduler,
            "schedules.self_schedule",
            group_name="Month Self Schedule Only",
            description="May schedule and view only their own monthly shifts.",
        )
        department_id = department.id

    monkeypatch.setattr(
        schedule_routes,
        "default_timezone_date",
        lambda: date(2026, 8, 15),
    )
    with client:
        login(client, "month-self-only@example.com", "pass")
        response = client.get(
            f"/schedules/month?department_id={department_id}"
        )

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "August 2026" in body
    assert "month-self-only@example.com" in body
    assert "month-hidden-coworker@example.com" not in body


def test_team_schedule_month_rejects_unauthorized_department_filter(
    client,
    app,
    monkeypatch,
):
    with app.app_context():
        from app.models import Setting

        Setting.set_schedule_week_start_day(2)
        viewer = _create_user("month-department-scope@example.com")
        department_a = Department(
            name="Allowed Monthly Department", active=True
        )
        department_b = Department(
            name="Forbidden Monthly Department", active=True
        )
        db.session.add_all([department_a, department_b])
        db.session.flush()
        position_a = ShiftPosition(
            department_id=department_a.id,
            name="Allowed Monthly Shift Marker",
            active=True,
        )
        position_b = ShiftPosition(
            department_id=department_b.id,
            name="Forbidden Monthly Shift Marker",
            active=True,
        )
        db.session.add_all([position_a, position_b])
        db.session.flush()
        db.session.add(
            UserDepartmentMembership(
                user_id=viewer.id,
                department_id=department_a.id,
                is_primary=True,
            )
        )
        week_a = DepartmentScheduleWeek(
            department_id=department_a.id,
            week_start=date(2026, 7, 1),
            is_published=True,
            current_version=1,
        )
        week_b = DepartmentScheduleWeek(
            department_id=department_b.id,
            week_start=date(2026, 7, 1),
            is_published=True,
            current_version=1,
        )
        db.session.add_all([week_a, week_b])
        db.session.flush()
        db.session.add_all(
            [
                Shift(
                    schedule_week_id=week_a.id,
                    position_id=position_a.id,
                    shift_date=date(2026, 7, 2),
                    start_time=time(9, 0),
                    end_time=time(13, 0),
                    paid_hours=4,
                    assignment_mode=Shift.ASSIGNMENT_OPEN,
                    live_version=1,
                ),
                Shift(
                    schedule_week_id=week_b.id,
                    position_id=position_b.id,
                    shift_date=date(2026, 7, 2),
                    start_time=time(13, 0),
                    end_time=time(17, 0),
                    paid_hours=4,
                    assignment_mode=Shift.ASSIGNMENT_OPEN,
                    live_version=1,
                ),
            ]
        )
        db.session.commit()
        grant_permissions(
            viewer,
            "schedules.view_team",
            group_name="Month Department Scope",
            description="View schedules only for assigned departments.",
        )
        department_a_id = department_a.id
        department_b_id = department_b.id

    monkeypatch.setattr(
        schedule_routes,
        "default_timezone_date",
        lambda: date(2026, 8, 15),
    )
    with client:
        login(client, "month-department-scope@example.com", "pass")
        response = client.get(
            "/schedules/month"
            f"?department_id={department_b_id}&month=2026-07-15"
        )

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "July 2026" in body
    assert "Allowed Monthly Department" in body
    assert "Allowed Monthly Shift Marker" in body
    assert "Forbidden Monthly Department" not in body
    assert "Forbidden Monthly Shift Marker" not in body
    assert f'value="{department_a_id}" selected' in body
    assert "month=2026-08-01" in body


def test_schedule_setup_mutations_reject_cross_department_ids(client, app):
    with app.app_context():
        manager = _create_user("scoped-setup-manager@example.com")
        target = _create_user("scoped-setup-target@example.com")
        target.hourly_rate = 19
        target.desired_weekly_hours = 30
        target.max_weekly_hours = 35
        target.schedule_notes = "Original scheduling note"
        department_a = Department(name="Managed Department A", active=True)
        department_b = Department(name="Hidden Department B", active=True)
        db.session.add_all([department_a, department_b])
        db.session.flush()
        position_a = ShiftPosition(
            department_id=department_a.id,
            name="Managed Position A",
            active=True,
        )
        position_b = ShiftPosition(
            department_id=department_b.id,
            name="Hidden Position B",
            active=True,
        )
        db.session.add_all([position_a, position_b])
        db.session.flush()
        manager_membership = UserDepartmentMembership(
            user_id=manager.id,
            department_id=department_a.id,
            can_manage_department=True,
            is_primary=True,
        )
        target_membership_a = UserDepartmentMembership(
            user_id=target.id,
            department_id=department_a.id,
            is_primary=True,
        )
        target_membership_b = UserDepartmentMembership(
            user_id=target.id,
            department_id=department_b.id,
        )
        db.session.add_all(
            [
                manager_membership,
                target_membership_a,
                target_membership_b,
            ]
        )
        db.session.flush()
        eligibility_a = UserPositionEligibility(
            user_id=target.id,
            position_id=position_a.id,
            priority=10,
            active=True,
        )
        eligibility_b = UserPositionEligibility(
            user_id=target.id,
            position_id=position_b.id,
            priority=20,
            active=True,
        )
        db.session.add_all([eligibility_a, eligibility_b])
        db.session.commit()
        grant_permissions(
            manager,
            "schedules.manage_setup",
            group_name="Scoped Schedule Setup",
            description="Manage schedule setup only in assigned departments.",
        )
        target_id = target.id
        department_b_id = department_b.id
        position_b_id = position_b.id
        membership_b_id = target_membership_b.id
        eligibility_b_id = eligibility_b.id

    with client:
        login(client, "scoped-setup-manager@example.com", "pass")
        get_response = client.get(f"/schedules/users/{target_id}")
        forged_pay_response = client.post(
            f"/schedules/users/{target_id}",
            data={
                "action": "save_profile",
                "profile-hourly_rate": "99",
                "profile-desired_weekly_hours": "60",
                "profile-max_weekly_hours": "80",
                "profile-schedule_enabled": "y",
                "profile-schedule_notes": "Setup-only update",
            },
        )
        responses = [
            client.post(
                f"/schedules/users/{target_id}",
                data={
                    "action": "add_membership",
                    "membership-department_id": str(department_b_id),
                    "membership-reports_to_user_id": "0",
                },
            ),
            client.post(
                f"/schedules/users/{target_id}",
                data={
                    "action": "update_membership_access",
                    "membership_id": str(membership_b_id),
                    "can_manage_department": "1",
                    "can_auto_assign": "1",
                },
            ),
            client.post(
                f"/schedules/users/{target_id}",
                data={
                    "action": "remove_membership",
                    "membership_id": str(membership_b_id),
                },
            ),
            client.post(
                f"/schedules/users/{target_id}",
                data={
                    "action": "add_eligibility",
                    "eligibility-position_id": str(position_b_id),
                    "eligibility-priority": "99",
                    "eligibility-active": "y",
                },
            ),
            client.post(
                f"/schedules/users/{target_id}",
                data={
                    "action": "remove_eligibility",
                    "eligibility_id": str(eligibility_b_id),
                },
            ),
        ]

    assert get_response.status_code == 200
    assert "Managed Department A" in get_response.get_data(as_text=True)
    assert "Managed Position A" in get_response.get_data(as_text=True)
    assert "Hidden Department B" not in get_response.get_data(as_text=True)
    assert "Hidden Position B" not in get_response.get_data(as_text=True)
    assert forged_pay_response.status_code == 302
    assert [response.status_code for response in responses] == [403] * 5

    with app.app_context():
        target = db.session.get(User, target_id)
        assert target.hourly_rate == 19
        assert target.desired_weekly_hours == 30
        assert target.max_weekly_hours == 35
        assert target.schedule_notes == "Setup-only update"
        membership_b = db.session.get(
            UserDepartmentMembership,
            membership_b_id,
        )
        assert membership_b is not None
        assert membership_b.can_manage_department is False
        assert membership_b.can_auto_assign is False
        assert (
            db.session.get(UserPositionEligibility, eligibility_b_id)
            is not None
        )
        assert (
            UserDepartmentMembership.query.filter_by(
                user_id=target_id,
                department_id=department_b_id,
            ).count()
            == 1
        )
