import os
from datetime import date, time

from werkzeug.security import generate_password_hash

from app import db
from app.models import (
    Department,
    DepartmentScheduleWeek,
    Shift,
    ShiftPosition,
    TimeOffRequest,
    TradeboardClaim,
    User,
    UserDepartmentMembership,
    UserPositionEligibility,
)
from tests.permission_helpers import grant_permissions
from tests.utils import login


def create_user(app, email: str, password: str = "pass") -> int:
    with app.app_context():
        user = User(
            email=email,
            password=generate_password_hash(password),
            active=True,
        )
        db.session.add(user)
        db.session.commit()
        return user.id


def test_profile_saves_schedule_notification_preferences(client, app):
    user_id = create_user(app, "schedule-profile@example.com", "oldpass")

    with client:
        login(client, "schedule-profile@example.com", "oldpass")
        response = client.post(
            "/auth/profile",
            data={
                "phone_number": "2045551111",
                "notify_transfers": "y",
                "notify_schedule_post_email": "y",
                "notify_schedule_post_text": "y",
                "notify_schedule_changes_email": "y",
                "notify_tradeboard_text": "y",
            },
            follow_redirects=True,
        )

    assert response.status_code == 200

    with app.app_context():
        user = db.session.get(User, user_id)
        assert user.phone_number == "2045551111"
        assert user.notify_schedule_post_email is True
        assert user.notify_schedule_post_text is True
        assert user.notify_schedule_changes_email is True
        assert user.notify_tradeboard_text is True


def test_schedule_setup_and_user_settings_flow(client, app):
    target_user_id = create_user(app, "scheduled-user@example.com")
    admin_email = os.getenv("ADMIN_EMAIL", "admin@example.com")
    admin_pass = os.getenv("ADMIN_PASS", "adminpass")

    with client:
        login(client, admin_email, admin_pass)
        response = client.post(
            "/schedules/setup",
            data={
                "action": "add_department",
                "department-name": "Warehouse",
                "department-description": "Warehouse staff",
                "department-active": "y",
            },
            follow_redirects=True,
        )
        assert response.status_code == 200

        with app.app_context():
            department = Department.query.filter_by(name="Warehouse").first()
            assert department is not None
            department_id = department.id

        response = client.post(
            "/schedules/setup",
            data={
                "action": "add_position",
                "position-department_id": str(department_id),
                "position-name": "Loader",
                "position-description": "Loads orders",
                "position-default_color": "text-primary",
                "position-sort_order": "1",
                "position-active": "y",
            },
            follow_redirects=True,
        )
        assert response.status_code == 200

        with app.app_context():
            position = ShiftPosition.query.filter_by(name="Loader").first()
            assert position is not None
            position_id = position.id

        response = client.post(
            f"/schedules/users/{target_user_id}",
            data={
                "action": "save_profile",
                "profile-hourly_rate": "18.50",
                "profile-desired_weekly_hours": "32",
                "profile-max_weekly_hours": "40",
                "profile-schedule_enabled": "y",
                "profile-schedule_notes": "Prefers opening shifts",
            },
            follow_redirects=True,
        )
        assert response.status_code == 200

        response = client.post(
            f"/schedules/users/{target_user_id}",
            data={
                "action": "add_membership",
                "membership-department_id": str(department_id),
                "membership-role": "lead",
                "membership-reports_to_user_id": "0",
                "membership-can_auto_assign": "y",
                "membership-is_primary": "y",
            },
            follow_redirects=True,
        )
        assert response.status_code == 200

        with app.app_context():
            membership = UserDepartmentMembership.query.filter_by(
                user_id=target_user_id, department_id=department_id
            ).first()
            assert membership is not None
            membership_id = membership.id

        response = client.post(
            f"/schedules/users/{target_user_id}",
            data={
                "action": "update_membership_role",
                "membership_id": str(membership_id),
                "role": "assistant operations lead",
                "can_auto_assign": "1",
            },
            follow_redirects=True,
        )
        assert response.status_code == 200

        response = client.post(
            f"/schedules/users/{target_user_id}",
            data={
                "action": "add_eligibility",
                "eligibility-position_id": str(position_id),
                "eligibility-priority": "5",
                "eligibility-active": "y",
            },
            follow_redirects=True,
        )
        assert response.status_code == 200

    with app.app_context():
        target_user = db.session.get(User, target_user_id)
        assert target_user.hourly_rate == 18.5
        assert target_user.schedule_notes == "Prefers opening shifts"
        membership = UserDepartmentMembership.query.filter_by(
            user_id=target_user_id, department_id=department_id
        ).first()
        assert membership
        assert membership.role == "assistant operations lead"
        assert membership.can_auto_assign is True
        assert UserPositionEligibility.query.filter_by(
            user_id=target_user_id, position_id=position_id
        ).first()


def test_team_schedule_can_create_and_publish_shift(client, app):
    employee_id = create_user(app, "shift-worker@example.com")
    admin_email = os.getenv("ADMIN_EMAIL", "admin@example.com")
    admin_pass = os.getenv("ADMIN_PASS", "adminpass")

    with app.app_context():
        department = Department(name="Ops", active=True)
        db.session.add(department)
        db.session.commit()
        position = ShiftPosition(
            department_id=department.id,
            name="Operator",
            active=True,
        )
        db.session.add(position)
        db.session.commit()
        db.session.add(
            UserDepartmentMembership(
                user_id=employee_id,
                department_id=department.id,
                role="staff",
                is_primary=True,
            )
        )
        db.session.add(
            UserPositionEligibility(
                user_id=employee_id,
                position_id=position.id,
                priority=10,
                active=True,
            )
        )
        db.session.commit()
        department_id = department.id
        position_id = position.id

    with client:
        login(client, admin_email, admin_pass)
        response = client.post(
            f"/schedules?department_id={department_id}&week_start=2026-04-06",
            data={
                "action": "save_shift",
                "department_id": str(department_id),
                "week_start": "2026-04-06",
                "shift-shift_id": "",
                "shift-schedule_week_id": "",
                "shift-shift_date": "2026-04-07",
                "shift-assigned_user_id": str(employee_id),
                "shift-position_id": str(position_id),
                "shift-assignment_mode": "assigned",
                "shift-start_time": "09:00",
                "shift-end_time": "17:00",
                "shift-paid_hours": "",
                "shift-location_id": "0",
                "shift-event_id": "0",
                "shift-notes": "Day shift",
                "shift-color": "text-primary",
            },
            follow_redirects=True,
        )
        assert response.status_code == 200
        assert b"Shift saved." in response.data

        response = client.post(
            f"/schedules?department_id={department_id}&week_start=2026-04-06",
            data={
                "action": "publish_week",
                "department_id": str(department_id),
                "week_start": "2026-04-06",
            },
            follow_redirects=True,
        )
        assert response.status_code == 200
        assert b"Schedule week published." in response.data

    with app.app_context():
        week = DepartmentScheduleWeek.query.filter_by(
            department_id=department_id,
            week_start=date(2026, 4, 6),
        ).first()
        assert week is not None
        assert week.is_published is True
        shift = Shift.query.filter_by(schedule_week_id=week.id).first()
        assert shift is not None
        assert shift.assigned_user_id == employee_id
        assert shift.live_version == week.current_version


def test_auto_assign_uses_default_availability_and_preferred_hours_cap(client, app):
    employee_id = create_user(app, "autoassign-worker@example.com")
    admin_email = os.getenv("ADMIN_EMAIL", "admin@example.com")
    admin_pass = os.getenv("ADMIN_PASS", "adminpass")

    with app.app_context():
        department = Department(name="Auto Assign Ops", active=True)
        db.session.add(department)
        db.session.commit()
        position = ShiftPosition(
            department_id=department.id,
            name="Runner",
            active=True,
        )
        db.session.add(position)
        db.session.commit()

        employee = db.session.get(User, employee_id)
        employee.desired_weekly_hours = 24.0
        employee.max_weekly_hours = 0.0

        db.session.add(
            UserDepartmentMembership(
                user_id=employee_id,
                department_id=department.id,
                role="staff",
                is_primary=True,
            )
        )
        db.session.add(
            UserPositionEligibility(
                user_id=employee_id,
                position_id=position.id,
                priority=10,
                active=True,
            )
        )
        week = DepartmentScheduleWeek(
            department_id=department.id,
            week_start=date(2026, 4, 6),
            is_published=False,
            current_version=0,
        )
        db.session.add(week)
        db.session.commit()

        for day in range(4):
            db.session.add(
                Shift(
                    schedule_week_id=week.id,
                    position_id=position.id,
                    shift_date=date(2026, 4, 6 + day),
                    start_time=time(9, 0),
                    end_time=time(21, 0),
                    paid_hours=12.0,
                    assignment_mode=Shift.ASSIGNMENT_OPEN,
                    live_version=0,
                )
            )
        db.session.commit()
        department_id = department.id
        week_id = week.id

    with client:
        login(client, admin_email, admin_pass)
        response = client.post(
            f"/schedules?department_id={department_id}&week_start=2026-04-06",
            data={
                "action": "auto_assign",
                "department_id": str(department_id),
                "week_start": "2026-04-06",
            },
            follow_redirects=True,
        )
        assert response.status_code == 200
        assert b"Auto-assign complete. 2 shifts assigned, 2 left unassigned." in response.data

    with app.app_context():
        assigned_shifts = Shift.query.filter_by(assigned_user_id=employee_id).all()
        open_shifts = Shift.query.filter_by(
            schedule_week_id=week_id,
            assignment_mode=Shift.ASSIGNMENT_OPEN,
            assigned_user_id=None,
        ).all()
        assert len(assigned_shifts) == 2
        assert len(open_shifts) == 2


def test_team_schedule_supports_all_departments_and_user_filter(client, app):
    worker_a_id = create_user(app, "board-worker-a@example.com")
    worker_b_id = create_user(app, "board-worker-b@example.com")
    admin_email = os.getenv("ADMIN_EMAIL", "admin@example.com")
    admin_pass = os.getenv("ADMIN_PASS", "adminpass")

    with app.app_context():
        department_a = Department(name="Board A", active=True)
        department_b = Department(name="Board B", active=True)
        db.session.add_all([department_a, department_b])
        db.session.commit()
        position_a = ShiftPosition(
            department_id=department_a.id,
            name="Runner A",
            active=True,
        )
        position_b = ShiftPosition(
            department_id=department_b.id,
            name="Runner B",
            active=True,
        )
        db.session.add_all([position_a, position_b])
        db.session.commit()

        db.session.add_all(
            [
                UserDepartmentMembership(
                    user_id=worker_a_id,
                    department_id=department_a.id,
                    role="staff",
                    is_primary=True,
                ),
                UserDepartmentMembership(
                    user_id=worker_b_id,
                    department_id=department_b.id,
                    role="staff",
                    is_primary=True,
                ),
            ]
        )
        week_a = DepartmentScheduleWeek(
            department_id=department_a.id,
            week_start=date(2026, 4, 6),
            is_published=False,
            current_version=0,
        )
        week_b = DepartmentScheduleWeek(
            department_id=department_b.id,
            week_start=date(2026, 4, 6),
            is_published=False,
            current_version=0,
        )
        db.session.add_all([week_a, week_b])
        db.session.commit()
        db.session.add_all(
            [
                Shift(
                    schedule_week_id=week_a.id,
                    position_id=position_a.id,
                    assigned_user_id=worker_a_id,
                    shift_date=date(2026, 4, 7),
                    start_time=time(9, 0),
                    end_time=time(17, 0),
                    paid_hours=8.0,
                    assignment_mode=Shift.ASSIGNMENT_ASSIGNED,
                    live_version=0,
                ),
                Shift(
                    schedule_week_id=week_b.id,
                    position_id=position_b.id,
                    assigned_user_id=worker_b_id,
                    shift_date=date(2026, 4, 8),
                    start_time=time(10, 0),
                    end_time=time(18, 0),
                    paid_hours=8.0,
                    assignment_mode=Shift.ASSIGNMENT_ASSIGNED,
                    live_version=0,
                ),
            ]
        )
        db.session.commit()

    with client:
        login(client, admin_email, admin_pass)
        response = client.get(
            "/schedules?department_id=all&week_start=2026-04-06",
            follow_redirects=True,
        )
        assert response.status_code == 200
        assert b"All Departments | Apr 06 - 12, 2026" in response.data
        assert b"Board A" in response.data
        assert b"Board B" in response.data

        response = client.get(
            f"/schedules?department_id=all&user_id={worker_a_id}&week_start=2026-04-06",
            follow_redirects=True,
        )
        assert response.status_code == 200
        assert b"board-worker-a@example.com" in response.data
        assert b"Runner B" not in response.data


def test_auto_assign_access_is_department_scoped_and_role_independent(client, app):
    scheduler_id = create_user(app, "scoped-autoassign@example.com")
    worker_a_id = create_user(app, "scoped-worker-a@example.com")
    worker_b_id = create_user(app, "scoped-worker-b@example.com")

    with app.app_context():
        scheduler = db.session.get(User, scheduler_id)
        grant_permissions(
            scheduler,
            "schedules.view_team",
            "schedules.edit_team",
            "schedules.auto_assign",
            group_name="Scoped Auto Assign",
            description="Can view schedule boards and run scoped auto-assign.",
        )

        department_a = Department(name="Scoped Auto A", active=True)
        department_b = Department(name="Scoped Auto B", active=True)
        db.session.add_all([department_a, department_b])
        db.session.commit()

        position_a = ShiftPosition(
            department_id=department_a.id,
            name="Scoped Runner A",
            active=True,
        )
        position_b = ShiftPosition(
            department_id=department_b.id,
            name="Scoped Runner B",
            active=True,
        )
        db.session.add_all([position_a, position_b])
        db.session.commit()

        db.session.add_all(
            [
                UserDepartmentMembership(
                    user_id=scheduler_id,
                    department_id=department_a.id,
                    role="lead",
                    can_auto_assign=True,
                    is_primary=True,
                ),
                UserDepartmentMembership(
                    user_id=scheduler_id,
                    department_id=department_b.id,
                    role="lead",
                    can_auto_assign=False,
                ),
                UserDepartmentMembership(
                    user_id=worker_a_id,
                    department_id=department_a.id,
                    role="staff",
                    is_primary=True,
                ),
                UserDepartmentMembership(
                    user_id=worker_b_id,
                    department_id=department_b.id,
                    role="staff",
                    is_primary=True,
                ),
                UserPositionEligibility(
                    user_id=worker_a_id,
                    position_id=position_a.id,
                    priority=10,
                    active=True,
                ),
                UserPositionEligibility(
                    user_id=worker_b_id,
                    position_id=position_b.id,
                    priority=10,
                    active=True,
                ),
            ]
        )
        week_a = DepartmentScheduleWeek(
            department_id=department_a.id,
            week_start=date(2026, 4, 6),
            is_published=False,
            current_version=0,
        )
        week_b = DepartmentScheduleWeek(
            department_id=department_b.id,
            week_start=date(2026, 4, 6),
            is_published=False,
            current_version=0,
        )
        db.session.add_all([week_a, week_b])
        db.session.commit()

        db.session.add_all(
            [
                Shift(
                    schedule_week_id=week_a.id,
                    position_id=position_a.id,
                    shift_date=date(2026, 4, 7),
                    start_time=time(9, 0),
                    end_time=time(17, 0),
                    paid_hours=8.0,
                    assignment_mode=Shift.ASSIGNMENT_OPEN,
                    live_version=0,
                ),
                Shift(
                    schedule_week_id=week_b.id,
                    position_id=position_b.id,
                    shift_date=date(2026, 4, 8),
                    start_time=time(9, 0),
                    end_time=time(17, 0),
                    paid_hours=8.0,
                    assignment_mode=Shift.ASSIGNMENT_OPEN,
                    live_version=0,
                ),
            ]
        )
        db.session.commit()
        department_a_id = department_a.id
        department_b_id = department_b.id

    with client:
        login(client, "scoped-autoassign@example.com", "pass")
        response = client.post(
            f"/schedules?department_id={department_a_id}&week_start=2026-04-06",
            data={
                "action": "auto_assign",
                "department_id": str(department_a_id),
                "week_start": "2026-04-06",
            },
            follow_redirects=True,
        )
        assert response.status_code == 200
        assert b"Auto-assign complete. 1 shifts assigned, 0 left unassigned." in response.data

        forbidden_response = client.post(
            f"/schedules?department_id={department_b_id}&week_start=2026-04-06",
            data={
                "action": "auto_assign",
                "department_id": str(department_b_id),
                "week_start": "2026-04-06",
            },
            follow_redirects=False,
        )
        assert forbidden_response.status_code == 403

    with app.app_context():
        assigned_in_a = Shift.query.filter_by(assigned_user_id=worker_a_id).count()
        assigned_in_b = Shift.query.filter_by(assigned_user_id=worker_b_id).count()
        assert assigned_in_a == 1
        assert assigned_in_b == 0


def test_all_departments_auto_assign_only_processes_allowed_departments(client, app):
    scheduler_id = create_user(app, "bulk-autoassign@example.com")
    worker_a_id = create_user(app, "bulk-worker-a@example.com")
    worker_b_id = create_user(app, "bulk-worker-b@example.com")

    with app.app_context():
        scheduler = db.session.get(User, scheduler_id)
        grant_permissions(
            scheduler,
            "schedules.view_team",
            "schedules.edit_team",
            "schedules.auto_assign",
            group_name="Bulk Auto Assign",
            description="Can run all-department auto-assign within scoped departments.",
        )

        department_a = Department(name="Bulk Auto A", active=True)
        department_b = Department(name="Bulk Auto B", active=True)
        db.session.add_all([department_a, department_b])
        db.session.commit()

        position_a = ShiftPosition(
            department_id=department_a.id,
            name="Bulk Runner A",
            active=True,
        )
        position_b = ShiftPosition(
            department_id=department_b.id,
            name="Bulk Runner B",
            active=True,
        )
        db.session.add_all([position_a, position_b])
        db.session.commit()

        db.session.add_all(
            [
                UserDepartmentMembership(
                    user_id=scheduler_id,
                    department_id=department_a.id,
                    role="coordinator",
                    can_auto_assign=True,
                    is_primary=True,
                ),
                UserDepartmentMembership(
                    user_id=scheduler_id,
                    department_id=department_b.id,
                    role="coordinator",
                    can_auto_assign=False,
                ),
                UserDepartmentMembership(
                    user_id=worker_a_id,
                    department_id=department_a.id,
                    role="staff",
                    is_primary=True,
                ),
                UserDepartmentMembership(
                    user_id=worker_b_id,
                    department_id=department_b.id,
                    role="staff",
                    is_primary=True,
                ),
                UserPositionEligibility(
                    user_id=worker_a_id,
                    position_id=position_a.id,
                    priority=10,
                    active=True,
                ),
                UserPositionEligibility(
                    user_id=worker_b_id,
                    position_id=position_b.id,
                    priority=10,
                    active=True,
                ),
            ]
        )
        week_a = DepartmentScheduleWeek(
            department_id=department_a.id,
            week_start=date(2026, 4, 6),
            is_published=False,
            current_version=0,
        )
        week_b = DepartmentScheduleWeek(
            department_id=department_b.id,
            week_start=date(2026, 4, 6),
            is_published=False,
            current_version=0,
        )
        db.session.add_all([week_a, week_b])
        db.session.commit()

        db.session.add_all(
            [
                Shift(
                    schedule_week_id=week_a.id,
                    position_id=position_a.id,
                    shift_date=date(2026, 4, 7),
                    start_time=time(8, 0),
                    end_time=time(16, 0),
                    paid_hours=8.0,
                    assignment_mode=Shift.ASSIGNMENT_OPEN,
                    live_version=0,
                ),
                Shift(
                    schedule_week_id=week_b.id,
                    position_id=position_b.id,
                    shift_date=date(2026, 4, 8),
                    start_time=time(8, 0),
                    end_time=time(16, 0),
                    paid_hours=8.0,
                    assignment_mode=Shift.ASSIGNMENT_OPEN,
                    live_version=0,
                ),
            ]
        )
        db.session.commit()
        week_a_id = week_a.id
        week_b_id = week_b.id

    with client:
        login(client, "bulk-autoassign@example.com", "pass")
        response = client.post(
            "/schedules?department_id=all&week_start=2026-04-06",
            data={
                "action": "auto_assign",
                "department_id": "all",
                "week_start": "2026-04-06",
            },
            follow_redirects=True,
        )
        assert response.status_code == 200
        assert b"Processed 1 department(s): Bulk Auto A." in response.data
        assert b"1 shifts assigned, 0 left unassigned." in response.data

    with app.app_context():
        week_a_shift = Shift.query.filter_by(schedule_week_id=week_a_id).first()
        week_b_shift = Shift.query.filter_by(schedule_week_id=week_b_id).first()
        assert week_a_shift is not None
        assert week_b_shift is not None
        assert week_a_shift.assigned_user_id == worker_a_id
        assert week_b_shift.assigned_user_id is None


def test_time_off_request_and_review_flow(client, app):
    user_id = create_user(app, "timeoff-user@example.com")
    admin_email = os.getenv("ADMIN_EMAIL", "admin@example.com")
    admin_pass = os.getenv("ADMIN_PASS", "adminpass")

    with app.app_context():
        user = db.session.get(User, user_id)
        grant_permissions(
            user,
            "schedules.request_time_off",
            "schedules.view_self_time_off",
            group_name="Time Off Self",
            description="Can request time off.",
        )

    with client:
        login(client, "timeoff-user@example.com", "pass")
        response = client.post(
            "/schedules/time-off",
            data={
                "action": "submit_request",
                "request-start_date": "2026-04-15",
                "request-end_date": "2026-04-16",
                "request-start_time": "",
                "request-end_time": "",
                "request-reason": "Vacation",
            },
            follow_redirects=True,
        )
        assert response.status_code == 200
        assert b"Time-off request submitted." in response.data

    with app.app_context():
        request_obj = TimeOffRequest.query.filter_by(user_id=user_id).first()
        assert request_obj is not None
        request_id = request_obj.id

    with client:
        login(client, admin_email, admin_pass)
        response = client.post(
            "/schedules/time-off",
            data={
                "action": "review_request",
                "request_id": str(request_id),
                "review-status": "approved",
                "review-manager_note": "Approved",
            },
            follow_redirects=True,
        )
        assert response.status_code == 200
        assert b"Time-off request updated." in response.data

    with app.app_context():
        request_obj = db.session.get(TimeOffRequest, request_id)
        assert request_obj.status == TimeOffRequest.STATUS_APPROVED
        assert request_obj.manager_note == "Approved"


def test_tradeboard_claim_and_approval_flow(client, app):
    claimant_id = create_user(app, "claimant@example.com")
    admin_email = os.getenv("ADMIN_EMAIL", "admin@example.com")
    admin_pass = os.getenv("ADMIN_PASS", "adminpass")

    with app.app_context():
        claimant = db.session.get(User, claimant_id)
        grant_permissions(
            claimant,
            "schedules.view_tradeboard",
            "schedules.claim_tradeboard",
            group_name="Tradeboard Claimant",
            description="Can claim tradeboard shifts.",
        )

        department = Department(name="Concessions", active=True)
        db.session.add(department)
        db.session.commit()
        position = ShiftPosition(
            department_id=department.id,
            name="Cashier",
            active=True,
        )
        db.session.add(position)
        db.session.commit()
        db.session.add(
            UserDepartmentMembership(
                user_id=claimant_id,
                department_id=department.id,
                role="staff",
                is_primary=True,
            )
        )
        db.session.add(
            UserPositionEligibility(
                user_id=claimant_id,
                position_id=position.id,
                priority=8,
                active=True,
            )
        )
        db.session.commit()
        week = DepartmentScheduleWeek(
            department_id=department.id,
            week_start=date(2026, 4, 6),
            is_published=True,
            current_version=1,
        )
        db.session.add(week)
        db.session.commit()
        shift = Shift(
            schedule_week_id=week.id,
            position_id=position.id,
            shift_date=date(2026, 4, 8),
            start_time=time(10, 0),
            end_time=time(16, 0),
            paid_hours=6.0,
            assignment_mode=Shift.ASSIGNMENT_OPEN,
            live_version=1,
        )
        db.session.add(shift)
        db.session.commit()
        department_id = department.id
        shift_id = shift.id

    with client:
        login(client, "claimant@example.com", "pass")
        response = client.post(
            f"/schedules/tradeboard?department_id={department_id}&week_start=2026-04-06",
            data={"action": "claim_shift", "shift_id": str(shift_id)},
            follow_redirects=True,
        )
        assert response.status_code == 200
        assert b"Shift claim submitted." in response.data

    with app.app_context():
        claim = TradeboardClaim.query.filter_by(
            shift_id=shift_id, user_id=claimant_id
        ).first()
        assert claim is not None
        claim_id = claim.id

    with client:
        login(client, admin_email, admin_pass)
        response = client.post(
            f"/schedules/tradeboard?department_id={department_id}&week_start=2026-04-06",
            data={
                "action": "review_claim",
                "claim_id": str(claim_id),
                "claimreview-status": "approved",
                "claimreview-manager_note": "Looks good",
            },
            follow_redirects=True,
        )
        assert response.status_code == 200
        assert b"Tradeboard claim updated." in response.data

    with app.app_context():
        shift = db.session.get(Shift, shift_id)
        claim = db.session.get(TradeboardClaim, claim_id)
        assert claim.status == TradeboardClaim.STATUS_APPROVED
        assert shift.assigned_user_id == claimant_id
        assert shift.assignment_mode == Shift.ASSIGNMENT_ASSIGNED
