import os
import re
from datetime import date, time

from werkzeug.security import generate_password_hash

from app import db
from app.models import (
    ActivityLog,
    Department,
    DepartmentScheduleWeek,
    Event,
    EventLocation,
    Location,
    ScheduleTemplate,
    ScheduleTemplateEntry,
    Shift,
    ShiftPosition,
    TimeOffRequest,
    TradeboardClaim,
    User,
    UserDepartmentMembership,
    UserFilterPreference,
    UserPositionEligibility,
)
from app.routes import schedule_routes as schedule_routes_module
from app.services import schedule_service
from app.utils.activity import flush_activity_logs
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


def assert_response_contains(response, *markers: bytes | str) -> None:
    body = response.get_data(as_text=False)
    for marker in markers:
        expected = marker.encode() if isinstance(marker, str) else marker
        assert expected in body


def assert_response_not_contains(response, *markers: bytes | str) -> None:
    body = response.get_data(as_text=False)
    for marker in markers:
        unexpected = marker.encode() if isinstance(marker, str) else marker
        assert unexpected not in body


def capture_schedule_notifications(monkeypatch):
    sent = {"emails": [], "texts": []}

    def fake_send_email(to_address, subject, body):
        sent["emails"].append(
            {"to": to_address, "subject": subject, "body": body}
        )

    def fake_send_sms(to_number, body):
        sent["texts"].append({"to": to_number, "body": body})

    monkeypatch.setattr(schedule_service, "send_email", fake_send_email)
    monkeypatch.setattr(schedule_service, "send_sms", fake_send_sms)
    return sent


def test_profile_saves_schedule_notification_preferences(client, app):
    user_id = create_user(app, "schedule-profile@example.com", "oldpass")

    with client:
        login(client, "schedule-profile@example.com", "oldpass")
        response = client.post(
            "/auth/profile",
            data={
                "phone_number": "2045551111",
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


def test_schedule_notification_service_sends_posted_and_tradeboard_alerts(
    app, monkeypatch
):
    sent = capture_schedule_notifications(monkeypatch)

    with app.app_context():
        assigned_user = User(
            email="schedule-posted-worker@example.com",
            password=generate_password_hash("pass"),
            active=True,
            phone_number="2045551201",
            notify_schedule_post_email=True,
            notify_schedule_post_text=True,
        )
        tradeboard_user = User(
            email="tradeboard-alert@example.com",
            password=generate_password_hash("pass"),
            active=True,
            phone_number="2045551202",
            notify_tradeboard_email=True,
            notify_tradeboard_text=True,
            schedule_enabled=True,
        )
        department = Department(name="Schedule Notification Ops", active=True)
        db.session.add_all([assigned_user, tradeboard_user, department])
        db.session.flush()

        position = ShiftPosition(
            department_id=department.id,
            name="Bar",
            active=True,
        )
        db.session.add(position)
        db.session.flush()

        db.session.add(
            UserPositionEligibility(
                user_id=tradeboard_user.id,
                position_id=position.id,
                priority=10,
                active=True,
            )
        )

        week = DepartmentScheduleWeek(
            department_id=department.id,
            week_start=date(2026, 5, 4),
            is_published=True,
            current_version=1,
        )
        db.session.add(week)
        db.session.flush()

        assigned_shift = Shift(
            schedule_week_id=week.id,
            position_id=position.id,
            shift_date=date(2026, 5, 5),
            start_time=time(9, 0),
            end_time=time(17, 0),
            paid_hours=8.0,
            assignment_mode=Shift.ASSIGNMENT_ASSIGNED,
            assigned_user_id=assigned_user.id,
            live_version=1,
        )
        open_shift = Shift(
            schedule_week_id=week.id,
            position_id=position.id,
            shift_date=date(2026, 5, 6),
            start_time=time(11, 0),
            end_time=time(15, 0),
            paid_hours=4.0,
            assignment_mode=Shift.ASSIGNMENT_OPEN,
            live_version=1,
        )
        db.session.add_all([assigned_shift, open_shift])
        db.session.commit()

        schedule_service.notify_schedule_posted(week, week.shifts)

    assert any(
        email["to"] == "schedule-posted-worker@example.com"
        and "Schedule posted:" in email["subject"]
        for email in sent["emails"]
    )
    assert any(
        text["to"] == "2045551201"
        and "Your schedule has been posted" in text["body"]
        for text in sent["texts"]
    )
    assert any(
        email["to"] == "tradeboard-alert@example.com"
        and "Tradeboard shifts available:" in email["subject"]
        for email in sent["emails"]
    )
    assert any(
        text["to"] == "2045551202"
        and "New open/tradeboard shifts are available" in text["body"]
        for text in sent["texts"]
    )


def test_schedule_notification_service_sends_change_and_time_off_alerts(
    app, monkeypatch
):
    sent = capture_schedule_notifications(monkeypatch)

    with app.app_context():
        old_user = User(
            email="schedule-old@example.com",
            password=generate_password_hash("pass"),
            active=True,
            phone_number="2045551203",
            notify_schedule_changes_email=True,
            notify_schedule_changes_text=True,
        )
        new_user = User(
            email="schedule-new@example.com",
            password=generate_password_hash("pass"),
            active=True,
            phone_number="2045551204",
            notify_schedule_changes_email=True,
            notify_schedule_changes_text=True,
        )
        tradeboard_user = User(
            email="schedule-tradeboard@example.com",
            password=generate_password_hash("pass"),
            active=True,
            phone_number="2045551205",
            notify_tradeboard_email=True,
            notify_tradeboard_text=True,
            schedule_enabled=True,
        )
        approver = User(
            email="schedule-approver@example.com",
            password=generate_password_hash("pass"),
            active=True,
        )
        department = Department(name="Schedule Change Ops", active=True)
        db.session.add_all([old_user, new_user, tradeboard_user, approver, department])
        db.session.flush()

        position = ShiftPosition(
            department_id=department.id,
            name="Cashier",
            active=True,
        )
        db.session.add(position)
        db.session.flush()

        db.session.add_all(
            [
                UserDepartmentMembership(
                    user_id=approver.id,
                    department_id=department.id,
                    role="manager",
                    is_primary=True,
                ),
                UserDepartmentMembership(
                    user_id=old_user.id,
                    department_id=department.id,
                    role="staff",
                    is_primary=True,
                ),
                UserPositionEligibility(
                    user_id=tradeboard_user.id,
                    position_id=position.id,
                    priority=10,
                    active=True,
                ),
            ]
        )

        grant_permissions(
            approver,
            "schedules.approve_time_off",
            group_name="Time Off Approver",
            description="Approves time off for schedule notification tests.",
        )

        week = DepartmentScheduleWeek(
            department_id=department.id,
            week_start=date(2026, 5, 11),
            is_published=True,
            current_version=2,
        )
        db.session.add(week)
        db.session.flush()

        assigned_shift = Shift(
            schedule_week_id=week.id,
            position_id=position.id,
            shift_date=date(2026, 5, 12),
            start_time=time(9, 0),
            end_time=time(17, 0),
            paid_hours=8.0,
            assignment_mode=Shift.ASSIGNMENT_ASSIGNED,
            assigned_user_id=old_user.id,
            live_version=2,
        )
        tradeboard_shift = Shift(
            schedule_week_id=week.id,
            position_id=position.id,
            shift_date=date(2026, 5, 13),
            start_time=time(12, 0),
            end_time=time(16, 0),
            paid_hours=4.0,
            assignment_mode=Shift.ASSIGNMENT_OPEN,
            notes="Initial coverage",
            live_version=2,
        )
        db.session.add_all([assigned_shift, tradeboard_shift])
        db.session.flush()

        before_assigned = schedule_service.capture_shift_snapshot(assigned_shift)
        assigned_shift.assigned_user_id = new_user.id
        before_tradeboard = schedule_service.capture_shift_snapshot(tradeboard_shift)
        tradeboard_shift.start_time = time(13, 0)
        db.session.commit()

        schedule_service.notify_schedule_changes(
            week,
            [
                (before_assigned, assigned_shift),
                (before_tradeboard, tradeboard_shift),
            ],
        )

        time_off_request = TimeOffRequest(
            user_id=old_user.id,
            start_date=date(2026, 5, 20),
            end_date=date(2026, 5, 21),
            reason="Family trip",
            status=TimeOffRequest.STATUS_PENDING,
        )
        db.session.add(time_off_request)
        db.session.commit()

        schedule_service.notify_time_off_submitted(time_off_request)

        time_off_request.status = TimeOffRequest.STATUS_APPROVED
        time_off_request.manager_note = "Approved"
        db.session.commit()

        schedule_service.notify_time_off_reviewed(time_off_request)

    assert any(
        email["to"] == "schedule-old@example.com"
        and "Schedule updated:" in email["subject"]
        and "Removed/changed:" in email["body"]
        for email in sent["emails"]
    )
    assert any(
        email["to"] == "schedule-new@example.com"
        and "Schedule updated:" in email["subject"]
        and "Updated:" in email["body"]
        for email in sent["emails"]
    )
    assert any(
        email["to"] == "schedule-tradeboard@example.com"
        and "Tradeboard updated:" in email["subject"]
        for email in sent["emails"]
    )
    assert any(
        text["to"] == "2045551205"
        and "Tradeboard/open shifts changed" in text["body"]
        for text in sent["texts"]
    )
    assert any(
        email["to"] == "schedule-approver@example.com"
        and email["subject"] == "Time-off request submitted"
        for email in sent["emails"]
    )
    assert any(
        email["to"] == "schedule-old@example.com"
        and email["subject"] == "Time-off request updated"
        and "was approved" in email["body"]
        for email in sent["emails"]
    )


def test_publish_and_published_shift_edit_trigger_schedule_notifiers(
    client, app, monkeypatch
):
    published_calls = []
    changed_calls = []

    def fake_notify_schedule_posted(schedule_week, shifts):
        published_calls.append((schedule_week.id, len(list(shifts))))

    def fake_notify_schedule_changes(schedule_week, change_records):
        changed_calls.append((schedule_week.id, len(change_records)))

    monkeypatch.setattr(
        schedule_routes_module, "notify_schedule_posted", fake_notify_schedule_posted
    )
    monkeypatch.setattr(
        schedule_routes_module, "notify_schedule_changes", fake_notify_schedule_changes
    )

    employee_id = create_user(app, "schedule-trigger-worker@example.com")
    admin_email = os.getenv("ADMIN_EMAIL", "admin@example.com")
    admin_pass = os.getenv("ADMIN_PASS", "adminpass")

    with app.app_context():
        department = Department(name="Schedule Trigger Ops", active=True)
        db.session.add(department)
        db.session.flush()

        position = ShiftPosition(
            department_id=department.id,
            name="Operator",
            active=True,
        )
        db.session.add(position)
        db.session.flush()

        db.session.add_all(
            [
                UserDepartmentMembership(
                    user_id=employee_id,
                    department_id=department.id,
                    role="staff",
                    is_primary=True,
                ),
                UserPositionEligibility(
                    user_id=employee_id,
                    position_id=position.id,
                    priority=10,
                    active=True,
                ),
            ]
        )
        db.session.commit()
        department_id = department.id
        position_id = position.id

    with client:
        login(client, admin_email, admin_pass)
        response = client.post(
            f"/schedules?department_id={department_id}&week_start=2026-05-18",
            data={
                "action": "save_shift",
                "department_id": str(department_id),
                "week_start": "2026-05-18",
                "shift-shift_id": "",
                "shift-schedule_week_id": "",
                "shift-shift_date": "2026-05-19",
                "shift-assigned_user_id": str(employee_id),
                "shift-position_id": str(position_id),
                "shift-assignment_mode": "assigned",
                "shift-start_time": "09:00",
                "shift-end_time": "17:00",
                "shift-paid_hours": "",
                "shift-location_id": "0",
                "shift-event_id": "0",
                "shift-notes": "Initial shift",
                "shift-color": "text-primary",
            },
            follow_redirects=True,
        )
        assert response.status_code == 200

        response = client.post(
            f"/schedules?department_id={department_id}&week_start=2026-05-18",
            data={
                "action": "publish_week",
                "department_id": str(department_id),
                "week_start": "2026-05-18",
            },
            follow_redirects=True,
        )
        assert response.status_code == 200

    with app.app_context():
        week = DepartmentScheduleWeek.query.filter_by(
            department_id=department_id,
            week_start=date(2026, 5, 18),
        ).one()
        shift = Shift.query.filter_by(schedule_week_id=week.id).one()
        shift_id = shift.id

    with client:
        login(client, admin_email, admin_pass)
        response = client.post(
            f"/schedules?department_id={department_id}&week_start=2026-05-18",
            data={
                "action": "save_shift",
                "department_id": str(department_id),
                "week_start": "2026-05-18",
                "shift-shift_id": str(shift_id),
                "shift-schedule_week_id": str(week.id),
                "shift-shift_date": "2026-05-19",
                "shift-assigned_user_id": str(employee_id),
                "shift-position_id": str(position_id),
                "shift-assignment_mode": "assigned",
                "shift-start_time": "10:00",
                "shift-end_time": "18:00",
                "shift-paid_hours": "",
                "shift-location_id": "0",
                "shift-event_id": "0",
                "shift-notes": "Updated shift",
                "shift-color": "text-primary",
            },
            follow_redirects=True,
        )
        assert response.status_code == 200

    assert published_calls
    assert published_calls[0][1] == 1
    assert changed_calls
    assert changed_calls[0][1] >= 1


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
            "/schedules/setup",
            data={
                "action": "add_membership_role",
                "membership_role-name": "lead",
                "membership_role-is_management": "y",
            },
            follow_redirects=True,
        )
        assert response.status_code == 200

        response = client.post(
            "/schedules/setup",
            data={
                "action": "add_membership_role",
                "membership_role-name": "assistant operations lead",
                "membership_role-is_management": "y",
            },
            follow_redirects=True,
        )
        assert response.status_code == 200

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


def test_schedule_role_catalog_scopes_gm_visibility_by_department(client, app):
    gm_user_id = create_user(app, "gm-scope@example.com")
    worker_a_id = create_user(app, "gm-scope-worker-a@example.com")
    worker_b_id = create_user(app, "gm-scope-worker-b@example.com")

    with app.app_context():
        gm_user = db.session.get(User, gm_user_id)
        grant_permissions(
            gm_user,
            "schedules.view_team",
            group_name="GM Scoped Schedule",
            description="Can view team schedules within assigned departments.",
        )

        department_a = Department(name="Scoped GM A", active=True)
        department_b = Department(name="Scoped GM B", active=True)
        db.session.add_all([department_a, department_b])
        db.session.flush()

        position_a = ShiftPosition(
            department_id=department_a.id,
            name="Scoped GM Position A",
            active=True,
        )
        position_b = ShiftPosition(
            department_id=department_b.id,
            name="Scoped GM Position B",
            active=True,
        )
        db.session.add_all([position_a, position_b])
        db.session.flush()

        db.session.add_all(
            [
                UserDepartmentMembership(
                    user_id=gm_user_id,
                    department_id=department_a.id,
                    role=UserDepartmentMembership.ROLE_GM,
                    is_primary=True,
                ),
                UserDepartmentMembership(
                    user_id=worker_a_id,
                    department_id=department_a.id,
                    role=UserDepartmentMembership.ROLE_STAFF,
                    is_primary=True,
                ),
                UserDepartmentMembership(
                    user_id=worker_b_id,
                    department_id=department_b.id,
                    role=UserDepartmentMembership.ROLE_STAFF,
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
        db.session.flush()

        db.session.add_all(
            [
                Shift(
                    schedule_week_id=week_a.id,
                    position_id=position_a.id,
                    shift_date=date(2026, 4, 7),
                    start_time=time(9, 0),
                    end_time=time(17, 0),
                    paid_hours=8.0,
                    assignment_mode=Shift.ASSIGNMENT_ASSIGNED,
                    assigned_user_id=worker_a_id,
                    live_version=0,
                ),
                Shift(
                    schedule_week_id=week_b.id,
                    position_id=position_b.id,
                    shift_date=date(2026, 4, 8),
                    start_time=time(10, 0),
                    end_time=time(18, 0),
                    paid_hours=8.0,
                    assignment_mode=Shift.ASSIGNMENT_ASSIGNED,
                    assigned_user_id=worker_b_id,
                    live_version=0,
                ),
            ]
        )
        db.session.commit()

    with client:
        login(client, "gm-scope@example.com", "pass")
        response = client.get(
            "/schedules?week_start=2026-04-06",
            follow_redirects=True,
        )

    assert response.status_code == 200
    assert b"Scoped GM A" in response.data
    assert b"gm-scope-worker-a@example.com" in response.data
    assert b"Scoped GM B" not in response.data
    assert b"gm-scope-worker-b@example.com" not in response.data


def test_schedule_setup_hides_manage_controls_for_pay_rate_only_users(client, app):
    with app.app_context():
        viewer = User(
            email="schedule-pay-rate-viewer@example.com",
            password=generate_password_hash("pass"),
            active=True,
        )
        department = Department(name="Read Only Ops", active=True)
        position = ShiftPosition(
            department=department,
            name="Reader",
            active=True,
        )
        target_user = User(
            email="schedule-target@example.com",
            password=generate_password_hash("pass"),
            active=True,
        )
        db.session.add_all([viewer, department, position, target_user])
        db.session.commit()
        grant_permissions(
            viewer,
            "schedules.manage_pay_rates",
            group_name="Schedule Pay Rates Only",
            description="Can edit pay rates without setup access.",
        )
        db.session.add(
            UserDepartmentMembership(
                user_id=viewer.id,
                department_id=department.id,
                role="manager",
                is_primary=True,
            )
        )
        db.session.commit()
        target_user_id = target_user.id

    with client:
        login(client, "schedule-pay-rate-viewer@example.com", "pass")
        response = client.get("/schedules/setup", follow_redirects=True)

    assert response.status_code == 200
    assert b"Add Department" not in response.data
    assert b"Add Position" not in response.data
    assert b'name="action" value="toggle_department"' not in response.data
    assert b'name="action" value="toggle_position"' not in response.data
    assert b"User Scheduling Profiles" in response.data


def test_schedule_user_settings_hide_setup_controls_for_pay_rate_only_users(client, app):
    with app.app_context():
        viewer = User(
            email="schedule-pay-rate-viewer-2@example.com",
            password=generate_password_hash("pass"),
            active=True,
        )
        department = Department(name="Pay Rates Only", active=True)
        position = ShiftPosition(
            department=department,
            name="Counter",
            active=True,
        )
        target_user = User(
            email="schedule-settings-target@example.com",
            password=generate_password_hash("pass"),
            active=True,
        )
        db.session.add_all([viewer, department, position, target_user])
        db.session.flush()
        db.session.add(
            UserDepartmentMembership(
                user_id=target_user.id,
                department_id=department.id,
                role="assistant",
                is_primary=True,
                can_auto_assign=True,
            )
        )
        db.session.add(
            UserPositionEligibility(
                user_id=target_user.id,
                position_id=position.id,
                priority=10,
                active=True,
            )
        )
        db.session.commit()
        grant_permissions(
            viewer,
            "schedules.manage_pay_rates",
            group_name="Schedule User Pay Rates Only",
            description="Can edit pay rates without setup access.",
        )
        db.session.add(
            UserDepartmentMembership(
                user_id=viewer.id,
                department_id=department.id,
                role="manager",
                is_primary=True,
            )
        )
        db.session.commit()
        target_user_id = target_user.id

    with client:
        login(client, "schedule-pay-rate-viewer-2@example.com", "pass")
        response = client.get(f"/schedules/users/{target_user_id}", follow_redirects=True)

    assert response.status_code == 200
    assert b"Hourly Rate" in response.data
    assert b"Save Scheduling Settings" in response.data
    assert b"Schedule Enabled" not in response.data
    assert b"Schedule Notes" not in response.data
    assert b'name="action" value="add_membership"' not in response.data
    assert b'name="action" value="add_eligibility"' not in response.data
    assert b'name="action" value="remove_membership"' not in response.data
    assert b'name="action" value="remove_eligibility"' not in response.data


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


def test_team_schedule_modal_renders_all_department_positions(client, app):
    admin_email = os.getenv("ADMIN_EMAIL", "admin@example.com")
    admin_pass = os.getenv("ADMIN_PASS", "adminpass")

    with app.app_context():
        department = Department(name="Modal Position Ops", active=True)
        db.session.add(department)
        db.session.commit()
        db.session.add_all(
            [
                ShiftPosition(
                    department_id=department.id,
                    name="Cashier",
                    active=True,
                    sort_order=1,
                ),
                ShiftPosition(
                    department_id=department.id,
                    name="Runner",
                    active=True,
                    sort_order=2,
                ),
            ]
        )
        db.session.commit()
        department_id = department.id

    with client:
        login(client, admin_email, admin_pass)
        response = client.get(
            f"/schedules?department_id={department_id}&week_start=2026-04-06&view_mode=position",
            follow_redirects=True,
        )
        assert response.status_code == 200
        assert b'app-page-shell' in response.data
        assert b'app-card' in response.data
        assert b"Cashier" in response.data
        assert b"Runner" in response.data
        assert b'data-department-position-map=' in response.data
        assert b'data-user-position-map-by-department=' in response.data
        assert b'name="shift-department_id"' in response.data
        assert b"Save Department as Default" in response.data


def test_team_schedule_view_only_user_cannot_see_or_save_shift_controls(client, app):
    viewer_id = create_user(app, "schedule-view-only@example.com")

    with app.app_context():
        viewer = db.session.get(User, viewer_id)
        grant_permissions(
            viewer,
            "schedules.view_team",
            group_name="Schedule View Only",
            description="Can view team schedules without editing shifts.",
        )

        department = Department(name="View Only Schedule Ops", active=True)
        db.session.add(department)
        db.session.flush()

        position = ShiftPosition(
            department_id=department.id,
            name="View Only Cashier",
            active=True,
        )
        db.session.add(position)
        db.session.add(
            UserDepartmentMembership(
                user_id=viewer_id,
                department_id=department.id,
                role="staff",
                is_primary=True,
            )
        )
        db.session.commit()
        department_id = department.id
        position_id = position.id

    with client:
        login(client, "schedule-view-only@example.com", "pass")
        for view_mode in ("user", "position"):
            response = client.get(
                f"/schedules?department_id={department_id}&week_start=2026-04-06&view_mode={view_mode}",
                follow_redirects=True,
            )
            assert response.status_code == 200
            assert b"Add Shift" not in response.data
            assert b'scheduleShiftModal"' not in response.data
            assert b'name="action" value="save_shift"' not in response.data

        response = client.post(
            f"/schedules?department_id={department_id}&week_start=2026-04-06",
            data={
                "action": "save_shift",
                "page_department_id": str(department_id),
                "week_start": "2026-04-06",
                "view_mode": "user",
                "shift-shift_id": "",
                "shift-schedule_week_id": "",
                "shift-shift_date": "2026-04-07",
                "shift-department_id": str(department_id),
                "shift-assigned_user_id": str(viewer_id),
                "shift-position_id": str(position_id),
                "shift-assignment_mode": "assigned",
                "shift-start_time": "09:00",
                "shift-end_time": "17:00",
                "shift-paid_hours": "",
                "shift-location_id": "0",
                "shift-event_id": "0",
                "shift-notes": "",
                "shift-color": "",
                "shift-copy_count": "1",
                "shift-repeat_weeks": "0",
                "shift-target_days": ["1"],
            },
            follow_redirects=False,
        )
        assert response.status_code == 403


def test_team_schedule_self_schedule_user_can_still_save_own_shift(client, app):
    scheduler_id = create_user(app, "self-scheduler@example.com")

    with app.app_context():
        scheduler = db.session.get(User, scheduler_id)
        grant_permissions(
            scheduler,
            "schedules.self_schedule",
            group_name="Self Scheduler",
            description="Can create and edit their own assigned shifts.",
        )

        department = Department(name="Self Schedule Ops", active=True)
        db.session.add(department)
        db.session.flush()

        position = ShiftPosition(
            department_id=department.id,
            name="Self Schedule Cashier",
            active=True,
        )
        db.session.add(position)
        db.session.add_all(
            [
                UserDepartmentMembership(
                    user_id=scheduler_id,
                    department_id=department.id,
                    role="staff",
                    is_primary=True,
                ),
                UserPositionEligibility(
                    user_id=scheduler_id,
                    position_id=position.id,
                    priority=10,
                    active=True,
                ),
            ]
        )
        db.session.commit()
        department_id = department.id
        position_id = position.id

    with client:
        login(client, "self-scheduler@example.com", "pass")
        response = client.get(
            f"/schedules?department_id={department_id}&week_start=2026-04-06",
            follow_redirects=True,
        )
        assert response.status_code == 200
        assert b'name="action" value="save_shift"' in response.data
        assert b"Add Shift" in response.data

        response = client.post(
            f"/schedules?department_id={department_id}&week_start=2026-04-06",
            data={
                "action": "save_shift",
                "page_department_id": str(department_id),
                "week_start": "2026-04-06",
                "view_mode": "user",
                "shift-shift_id": "",
                "shift-schedule_week_id": "",
                "shift-shift_date": "2026-04-07",
                "shift-department_id": str(department_id),
                "shift-assigned_user_id": str(scheduler_id),
                "shift-position_id": str(position_id),
                "shift-assignment_mode": "assigned",
                "shift-start_time": "09:00",
                "shift-end_time": "17:00",
                "shift-paid_hours": "",
                "shift-location_id": "0",
                "shift-event_id": "0",
                "shift-notes": "",
                "shift-color": "",
                "shift-copy_count": "1",
                "shift-repeat_weeks": "0",
                "shift-target_days": ["1"],
            },
            follow_redirects=True,
        )
        assert response.status_code == 200
        assert b"Shift saved." in response.data

    with app.app_context():
        shift = Shift.query.filter_by(
            assigned_user_id=scheduler_id,
            position_id=position_id,
            shift_date=date(2026, 4, 7),
        ).first()
        assert shift is not None


def test_schedule_templates_apply_only_user_hides_management_controls(client, app):
    planner_id = create_user(app, "template-apply-only@example.com")

    with app.app_context():
        planner = db.session.get(User, planner_id)
        grant_permissions(
            planner,
            "schedules.apply_templates",
            group_name="Template Apply Only",
            description="Can apply schedule templates without managing them.",
        )

        department = Department(name="Template Apply Ops", active=True)
        db.session.add(department)
        db.session.flush()

        position = ShiftPosition(
            department_id=department.id,
            name="Template Apply Cashier",
            active=True,
        )
        db.session.add(position)
        db.session.flush()

        template = ScheduleTemplate(
            name="Apply Only Template",
            department_id=department.id,
            position_id=position.id,
            span=ScheduleTemplate.SPAN_WEEK,
            active=True,
            created_by=planner,
            updated_by=planner,
        )
        db.session.add_all(
            [
                UserDepartmentMembership(
                    user_id=planner_id,
                    department_id=department.id,
                    role=UserDepartmentMembership.ROLE_MANAGER,
                    is_primary=True,
                ),
                template,
            ]
        )
        db.session.flush()

        db.session.add(
            ScheduleTemplateEntry(
                template_id=template.id,
                weekday=0,
                assignment_mode=Shift.ASSIGNMENT_OPEN,
                start_time=time(9, 0),
                end_time=time(17, 0),
                paid_hours=8.0,
            )
        )
        db.session.commit()
        template_id = template.id

    with client:
        login(client, "template-apply-only@example.com", "pass")
        response = client.get("/schedules/templates", follow_redirects=True)

    assert response.status_code == 200
    assert_response_contains(
        response,
        "Apply Only Template",
        'name="action" value="apply_templates"',
        "Apply to Draft Schedule",
    )
    assert_response_not_contains(
        response,
        "Create Template",
        'name="action" value="create_template"',
        'name="action" value="toggle_template"',
        'name="action" value="delete_template"',
    )

    with client:
        login(client, "template-apply-only@example.com", "pass")
        detail_response = client.get(
            f"/schedules/templates/{template_id}",
            follow_redirects=True,
        )

    assert detail_response.status_code == 200
    assert_response_contains(
        detail_response,
        "Editing them requires the schedule-template manage permission.",
    )
    assert_response_not_contains(
        detail_response,
        'name="action" value="update_template"',
        'name="action" value="save_entry"',
        'name="action" value="delete_entry"',
        "Save Template",
        "Save Template Shift",
    )


def test_tradeboard_claim_only_user_hides_review_controls(client, app):
    claimant_id = create_user(app, "tradeboard-claim-only@example.com")
    other_user_id = create_user(app, "tradeboard-other@example.com")

    with app.app_context():
        claimant = db.session.get(User, claimant_id)
        grant_permissions(
            claimant,
            "schedules.view_tradeboard",
            "schedules.claim_tradeboard",
            group_name="Tradeboard Claim Only",
            description="Can view and claim tradeboard shifts.",
        )

        department = Department(name="Tradeboard Claim Ops", active=True)
        db.session.add(department)
        db.session.flush()

        position = ShiftPosition(
            department_id=department.id,
            name="Tradeboard Claim Cashier",
            active=True,
        )
        db.session.add(position)
        db.session.flush()

        schedule_week = DepartmentScheduleWeek(
            department_id=department.id,
            week_start=date(2026, 4, 6),
            is_published=True,
            current_version=1,
        )
        db.session.add_all(
            [
                schedule_week,
                UserDepartmentMembership(
                    user_id=claimant_id,
                    department_id=department.id,
                    role=UserDepartmentMembership.ROLE_STAFF,
                    is_primary=True,
                ),
                UserDepartmentMembership(
                    user_id=other_user_id,
                    department_id=department.id,
                    role=UserDepartmentMembership.ROLE_STAFF,
                    is_primary=True,
                ),
                UserPositionEligibility(
                    user_id=claimant_id,
                    position_id=position.id,
                    priority=10,
                    active=True,
                ),
            ]
        )
        db.session.flush()

        shift = Shift(
            schedule_week_id=schedule_week.id,
            position_id=position.id,
            shift_date=date(2026, 4, 7),
            start_time=time(9, 0),
            end_time=time(17, 0),
            paid_hours=8.0,
            assignment_mode=Shift.ASSIGNMENT_OPEN,
            live_version=1,
        )
        db.session.add(shift)
        db.session.flush()

        db.session.add(
            TradeboardClaim(
                shift_id=shift.id,
                user_id=other_user_id,
                status=TradeboardClaim.STATUS_PENDING,
            )
        )
        db.session.commit()
        department_id = department.id

    with client:
        login(client, "tradeboard-claim-only@example.com", "pass")
        response = client.get(
            f"/schedules/tradeboard?department_id={department_id}&week_start=2026-04-06",
            follow_redirects=True,
        )

    assert response.status_code == 200
    assert_response_contains(response, 'name="action" value="claim_shift"', "Tradeboard")
    assert_response_not_contains(
        response,
        "Pending Claims",
        'name="action" value="review_claim"',
        "Submit Decision",
    )


def test_tradeboard_approver_only_user_hides_claim_controls(client, app):
    approver_id = create_user(app, "tradeboard-approver@example.com")
    claimant_id = create_user(app, "tradeboard-pending-claimant@example.com")

    with app.app_context():
        approver = db.session.get(User, approver_id)
        grant_permissions(
            approver,
            "schedules.view_tradeboard",
            "schedules.approve_tradeboard",
            group_name="Tradeboard Approver",
            description="Can review tradeboard claims without claiming shifts.",
        )

        department = Department(name="Tradeboard Review Ops", active=True)
        db.session.add(department)
        db.session.flush()

        position = ShiftPosition(
            department_id=department.id,
            name="Tradeboard Review Cashier",
            active=True,
        )
        db.session.add(position)
        db.session.flush()

        schedule_week = DepartmentScheduleWeek(
            department_id=department.id,
            week_start=date(2026, 4, 6),
            is_published=True,
            current_version=1,
        )
        db.session.add_all(
            [
                schedule_week,
                UserDepartmentMembership(
                    user_id=approver_id,
                    department_id=department.id,
                    role=UserDepartmentMembership.ROLE_MANAGER,
                    is_primary=True,
                ),
                UserDepartmentMembership(
                    user_id=claimant_id,
                    department_id=department.id,
                    role=UserDepartmentMembership.ROLE_STAFF,
                    is_primary=True,
                ),
            ]
        )
        db.session.flush()

        shift = Shift(
            schedule_week_id=schedule_week.id,
            position_id=position.id,
            shift_date=date(2026, 4, 7),
            start_time=time(10, 0),
            end_time=time(18, 0),
            paid_hours=8.0,
            assignment_mode=Shift.ASSIGNMENT_TRADEBOARD,
            live_version=1,
        )
        db.session.add(shift)
        db.session.flush()

        db.session.add(
            TradeboardClaim(
                shift_id=shift.id,
                user_id=claimant_id,
                status=TradeboardClaim.STATUS_PENDING,
            )
        )
        db.session.commit()
        department_id = department.id

    with client:
        login(client, "tradeboard-approver@example.com", "pass")
        response = client.get(
            f"/schedules/tradeboard?department_id={department_id}&week_start=2026-04-06",
            follow_redirects=True,
        )

    assert response.status_code == 200
    assert_response_contains(
        response,
        "Pending Claims",
        'name="action" value="review_claim"',
        "Submit Decision",
    )
    assert_response_not_contains(
        response,
        'name="action" value="claim_shift"',
        'name="action" value="cancel_claim"',
    )


def test_team_schedule_defaults_to_all_departments_for_new_users(client, app):
    admin_email = os.getenv("ADMIN_EMAIL", "admin@example.com")
    admin_pass = os.getenv("ADMIN_PASS", "adminpass")

    with app.app_context():
        db.session.add_all(
            [
                Department(name="Default Dept A", active=True),
                Department(name="Default Dept B", active=True),
            ]
        )
        db.session.commit()

    with client:
        login(client, admin_email, admin_pass)
        response = client.get("/schedules?week_start=2026-04-06", follow_redirects=True)
        assert response.status_code == 200
        assert b"All Departments | Apr 06 - 12, 2026" in response.data
        assert b'<option value="all" selected>All Departments</option>' in response.data
        assert b'name="page_department_id" value="all"' in response.data


def test_team_schedule_uses_saved_department_default_when_no_filter_supplied(
    client,
    app,
    save_filter_defaults,
):
    admin_email = os.getenv("ADMIN_EMAIL", "admin@example.com")
    admin_pass = os.getenv("ADMIN_PASS", "adminpass")

    with app.app_context():
        department_a = Department(name="Saved Default A", active=True)
        department_b = Department(name="Saved Default B", active=True)
        db.session.add_all([department_a, department_b])
        db.session.commit()
        department_b_id = department_b.id

    with client:
        login(client, admin_email, admin_pass)
        save_filter_defaults(
            "schedule.team_schedule",
            {"department_id": [str(department_b_id)]},
            token_path="/schedules?week_start=2026-04-06",
        )
        response = client.get("/schedules?week_start=2026-04-06", follow_redirects=True)
        assert response.status_code == 200
        assert b"Saved Default B | Apr 06 - 12, 2026" in response.data
        assert (
            f'<option value="{department_b_id}" selected>Saved Default B</option>'.encode()
            in response.data
        )


def test_team_schedule_page_renders_pin_default_save_token(client, app):
    admin_email = os.getenv("ADMIN_EMAIL", "admin@example.com")
    admin_pass = os.getenv("ADMIN_PASS", "adminpass")

    with app.app_context():
        department_a = Department(name="Pinned Default A", active=True)
        department_b = Department(name="Pinned Default B", active=True)
        db.session.add_all([department_a, department_b])
        db.session.commit()
        department_b_id = department_b.id

    with client:
        login(client, admin_email, admin_pass)
        response = client.get("/schedules?week_start=2026-04-06", follow_redirects=True)
        assert response.status_code == 200
        body = response.get_data(as_text=True)
        token_match = re.search(r'data-schedule-default-csrf="([^"]+)"', body)
        assert token_match is not None

        save_response = client.post(
            "/preferences/filters",
            data={
                "scope": "schedule.team_schedule",
                "department_id": str(department_b_id),
            },
            headers={"X-CSRFToken": token_match.group(1)},
        )
        assert save_response.status_code == 200

    with app.app_context():
        admin_user = User.query.filter_by(email=admin_email).one()
        preference = UserFilterPreference.query.filter_by(
            user_id=admin_user.id,
            scope="schedule.team_schedule",
        ).one()
        assert preference.values == {"department_id": [str(department_b_id)]}


def test_team_schedule_position_view_filters_by_event_and_location(client, app):
    worker_id = create_user(app, "position-filter-worker@example.com")
    admin_email = os.getenv("ADMIN_EMAIL", "admin@example.com")
    admin_pass = os.getenv("ADMIN_PASS", "adminpass")

    with app.app_context():
        department = Department(name="Position Board Ops", active=True)
        location_a = Location(name="North Stand")
        location_b = Location(name="South Stand")
        event_a = Event(
            name="Morning Event",
            start_date=date(2026, 4, 6),
            end_date=date(2026, 4, 12),
        )
        event_b = Event(
            name="Evening Event",
            start_date=date(2026, 4, 6),
            end_date=date(2026, 4, 12),
        )
        db.session.add_all([department, location_a, location_b, event_a, event_b])
        db.session.commit()

        db.session.add_all(
            [
                EventLocation(event_id=event_a.id, location_id=location_a.id),
                EventLocation(event_id=event_b.id, location_id=location_b.id),
            ]
        )
        db.session.commit()

        cashier = ShiftPosition(
            department_id=department.id,
            name="Cashier",
            active=True,
            sort_order=1,
        )
        runner = ShiftPosition(
            department_id=department.id,
            name="Runner",
            active=True,
            sort_order=2,
        )
        db.session.add_all([cashier, runner])
        db.session.commit()

        db.session.add_all(
            [
                UserDepartmentMembership(
                    user_id=worker_id,
                    department_id=department.id,
                    role="staff",
                    is_primary=True,
                ),
                UserPositionEligibility(
                    user_id=worker_id,
                    position_id=cashier.id,
                    priority=10,
                    active=True,
                ),
            ]
        )
        week = DepartmentScheduleWeek(
            department_id=department.id,
            week_start=date(2026, 4, 6),
            is_published=False,
            current_version=0,
        )
        db.session.add(week)
        db.session.commit()

        db.session.add_all(
            [
                Shift(
                    schedule_week_id=week.id,
                    position_id=cashier.id,
                    assigned_user_id=worker_id,
                    location_id=location_a.id,
                    event_id=event_a.id,
                    shift_date=date(2026, 4, 7),
                    start_time=time(9, 0),
                    end_time=time(17, 0),
                    paid_hours=8.0,
                    assignment_mode=Shift.ASSIGNMENT_ASSIGNED,
                    live_version=0,
                ),
                Shift(
                    schedule_week_id=week.id,
                    position_id=runner.id,
                    location_id=location_b.id,
                    event_id=event_b.id,
                    shift_date=date(2026, 4, 7),
                    start_time=time(10, 0),
                    end_time=time(14, 0),
                    paid_hours=4.0,
                    assignment_mode=Shift.ASSIGNMENT_OPEN,
                    live_version=0,
                ),
            ]
        )
        db.session.commit()
        department_id = department.id
        event_a_id = event_a.id
        location_a_id = location_a.id

    with client:
        login(client, admin_email, admin_pass)
        response = client.get(
            f"/schedules?department_id={department_id}&week_start=2026-04-06&view_mode=position&filter_event_id={event_a_id}&filter_location_id={location_a_id}",
            follow_redirects=True,
        )
        assert response.status_code == 200
        assert b"By Position View" in response.data
        assert b"Cashier" in response.data
        assert b"position-filter-worker@example.com" in response.data
        assert b"10:00 - 14:00" not in response.data
        assert b"modal-dialog-scrollable" in response.data
        assert b"schedule-shift-modal-form" in response.data


def test_team_schedule_save_shift_can_target_different_department_than_page_filter(
    client,
    app,
):
    admin_email = os.getenv("ADMIN_EMAIL", "admin@example.com")
    admin_pass = os.getenv("ADMIN_PASS", "adminpass")

    with app.app_context():
        department_a = Department(name="Page Filter Dept", active=True)
        department_b = Department(name="Target Shift Dept", active=True)
        db.session.add_all([department_a, department_b])
        db.session.commit()

        position_a = ShiftPosition(
            department_id=department_a.id,
            name="Page Filter Position",
            active=True,
        )
        position_b = ShiftPosition(
            department_id=department_b.id,
            name="Target Position",
            active=True,
        )
        db.session.add_all([position_a, position_b])
        db.session.commit()
        department_a_id = department_a.id
        department_b_id = department_b.id
        position_b_id = position_b.id

    with client:
        login(client, admin_email, admin_pass)
        response = client.post(
            f"/schedules?department_id={department_a_id}&week_start=2026-04-06&view_mode=position",
            data={
                "action": "save_shift",
                "page_department_id": str(department_a_id),
                "week_start": "2026-04-06",
                "view_mode": "position",
                "shift-shift_id": "",
                "shift-schedule_week_id": "",
                "shift-shift_date": "2026-04-07",
                "shift-department_id": str(department_b_id),
                "shift-assigned_user_id": "0",
                "shift-position_id": str(position_b_id),
                "shift-assignment_mode": "open",
                "shift-start_time": "09:00",
                "shift-end_time": "17:00",
                "shift-paid_hours": "",
                "shift-location_id": "0",
                "shift-event_id": "0",
                "shift-notes": "",
                "shift-color": "",
                "shift-copy_count": "1",
                "shift-repeat_weeks": "0",
                "shift-target_days": ["1"],
            },
            follow_redirects=True,
        )
        assert response.status_code == 200
        assert b"Shift saved." in response.data
        assert b"Target Shift Dept | Apr 06 - 12, 2026" in response.data

    with app.app_context():
        shift = Shift.query.filter_by(position_id=position_b_id).first()
        assert shift is not None
        assert shift.schedule_week.department_id == department_b_id


def test_team_schedule_bulk_create_repeats_across_days_weeks_and_copies(client, app):
    admin_email = os.getenv("ADMIN_EMAIL", "admin@example.com")
    admin_pass = os.getenv("ADMIN_PASS", "adminpass")

    with app.app_context():
        department = Department(name="Bulk Create Ops", active=True)
        location = Location(name="Bulk Create Stand")
        event = Event(
            name="Tournament Week",
            start_date=date(2026, 4, 6),
            end_date=date(2026, 4, 19),
        )
        db.session.add_all([department, location, event])
        db.session.commit()

        db.session.add(EventLocation(event_id=event.id, location_id=location.id))
        db.session.commit()

        position = ShiftPosition(
            department_id=department.id,
            name="Warehouse",
            active=True,
        )
        db.session.add(position)
        db.session.commit()
        department_id = department.id
        position_id = position.id
        event_id = event.id
        location_id = location.id

    with client:
        login(client, admin_email, admin_pass)
        response = client.post(
            f"/schedules?department_id={department_id}&week_start=2026-04-06&view_mode=position&filter_event_id={event_id}&filter_location_id={location_id}",
            data={
                "action": "save_shift",
                "department_id": str(department_id),
                "week_start": "2026-04-06",
                "view_mode": "position",
                "filter_event_id": str(event_id),
                "filter_location_id": str(location_id),
                "shift-shift_id": "",
                "shift-schedule_week_id": "",
                "shift-shift_date": "2026-04-07",
                "shift-assigned_user_id": "0",
                "shift-position_id": str(position_id),
                "shift-assignment_mode": "tradeboard",
                "shift-start_time": "09:00",
                "shift-end_time": "11:00",
                "shift-paid_hours": "",
                "shift-location_id": str(location_id),
                "shift-event_id": str(event_id),
                "shift-notes": "Coverage block",
                "shift-color": "text-primary",
                "shift-copy_count": "2",
                "shift-repeat_weeks": "1",
                "shift-target_days": ["1", "3"],
            },
            follow_redirects=True,
        )
        assert response.status_code == 200
        assert b"8 shifts saved." in response.data

    with app.app_context():
        shifts = (
            Shift.query.filter_by(position_id=position_id)
            .order_by(Shift.shift_date.asc(), Shift.id.asc())
            .all()
        )
        assert len(shifts) == 8
        assert {shift.assignment_mode for shift in shifts} == {Shift.ASSIGNMENT_TRADEBOARD}
        assert {shift.event_id for shift in shifts} == {event_id}
        assert {shift.location_id for shift in shifts} == {location_id}
        assert [shift.shift_date for shift in shifts].count(date(2026, 4, 7)) == 2
        assert [shift.shift_date for shift in shifts].count(date(2026, 4, 9)) == 2
        assert [shift.shift_date for shift in shifts].count(date(2026, 4, 14)) == 2
        assert [shift.shift_date for shift in shifts].count(date(2026, 4, 16)) == 2


def test_team_schedule_rejects_multiple_assigned_copies_per_day(client, app):
    worker_id = create_user(app, "assigned-copy-worker@example.com")
    admin_email = os.getenv("ADMIN_EMAIL", "admin@example.com")
    admin_pass = os.getenv("ADMIN_PASS", "adminpass")

    with app.app_context():
        department = Department(name="Assigned Copy Ops", active=True)
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
                user_id=worker_id,
                department_id=department.id,
                role="staff",
                is_primary=True,
            )
        )
        db.session.add(
            UserPositionEligibility(
                user_id=worker_id,
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
            f"/schedules?department_id={department_id}&week_start=2026-04-06&view_mode=position",
            data={
                "action": "save_shift",
                "department_id": str(department_id),
                "week_start": "2026-04-06",
                "view_mode": "position",
                "shift-shift_id": "",
                "shift-schedule_week_id": "",
                "shift-shift_date": "2026-04-07",
                "shift-assigned_user_id": str(worker_id),
                "shift-position_id": str(position_id),
                "shift-assignment_mode": "assigned",
                "shift-start_time": "09:00",
                "shift-end_time": "17:00",
                "shift-paid_hours": "",
                "shift-location_id": "0",
                "shift-event_id": "0",
                "shift-notes": "",
                "shift-color": "",
                "shift-copy_count": "2",
                "shift-repeat_weeks": "0",
                "shift-target_days": ["1"],
            },
            follow_redirects=True,
        )
        assert response.status_code == 200
        assert (
            b"Assigned shifts can only create one copy per selected day."
            in response.data
        )

    with app.app_context():
        assert Shift.query.filter_by(position_id=position_id).count() == 0


def test_team_schedule_rejects_assigned_user_without_position_eligibility(client, app):
    worker_id = create_user(app, "ineligible-position-worker@example.com")
    admin_email = os.getenv("ADMIN_EMAIL", "admin@example.com")
    admin_pass = os.getenv("ADMIN_PASS", "adminpass")

    with app.app_context():
        department = Department(name="Eligibility Guard Ops", active=True)
        db.session.add(department)
        db.session.commit()
        cashier = ShiftPosition(
            department_id=department.id,
            name="Cashier",
            active=True,
            sort_order=1,
        )
        runner = ShiftPosition(
            department_id=department.id,
            name="Runner",
            active=True,
            sort_order=2,
        )
        db.session.add_all([cashier, runner])
        db.session.commit()
        db.session.add(
            UserDepartmentMembership(
                user_id=worker_id,
                department_id=department.id,
                role="staff",
                is_primary=True,
            )
        )
        db.session.add(
            UserPositionEligibility(
                user_id=worker_id,
                position_id=cashier.id,
                priority=10,
                active=True,
            )
        )
        db.session.commit()
        department_id = department.id
        runner_id = runner.id

    with client:
        login(client, admin_email, admin_pass)
        response = client.post(
            f"/schedules?department_id={department_id}&week_start=2026-04-06&view_mode=position",
            data={
                "action": "save_shift",
                "department_id": str(department_id),
                "week_start": "2026-04-06",
                "view_mode": "position",
                "shift-shift_id": "",
                "shift-schedule_week_id": "",
                "shift-shift_date": "2026-04-07",
                "shift-assigned_user_id": str(worker_id),
                "shift-position_id": str(runner_id),
                "shift-assignment_mode": "assigned",
                "shift-start_time": "09:00",
                "shift-end_time": "17:00",
                "shift-paid_hours": "",
                "shift-location_id": "0",
                "shift-event_id": "0",
                "shift-notes": "",
                "shift-color": "",
                "shift-copy_count": "1",
                "shift-repeat_weeks": "0",
                "shift-target_days": ["1"],
            },
            follow_redirects=True,
        )
        assert response.status_code == 200
        assert b"Selected user is not eligible for that position." in response.data

    with app.app_context():
        assert Shift.query.filter(Shift.position_id == runner_id).count() == 0


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
        assert b"10:00 - 18:00" not in response.data


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


def test_all_departments_publish_only_processes_managed_departments(client, app):
    scheduler_id = create_user(app, "bulk-publish@example.com")

    with app.app_context():
        scheduler = db.session.get(User, scheduler_id)
        grant_permissions(
            scheduler,
            "schedules.view_team",
            "schedules.publish",
            group_name="Bulk Publish",
            description="Can publish schedule weeks within managed departments.",
        )

        department_a = Department(name="Bulk Publish A", active=True)
        department_b = Department(name="Bulk Publish B", active=True)
        db.session.add_all([department_a, department_b])
        db.session.flush()

        position_a = ShiftPosition(
            department_id=department_a.id,
            name="Publish Runner A",
            active=True,
        )
        position_b = ShiftPosition(
            department_id=department_b.id,
            name="Publish Runner B",
            active=True,
        )
        db.session.add_all([position_a, position_b])
        db.session.flush()

        db.session.add_all(
            [
                UserDepartmentMembership(
                    user_id=scheduler_id,
                    department_id=department_a.id,
                    role=UserDepartmentMembership.ROLE_MANAGER,
                    is_primary=True,
                ),
                UserDepartmentMembership(
                    user_id=scheduler_id,
                    department_id=department_b.id,
                    role=UserDepartmentMembership.ROLE_STAFF,
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
        db.session.flush()

        shift_a = Shift(
            schedule_week_id=week_a.id,
            position_id=position_a.id,
            shift_date=date(2026, 4, 7),
            start_time=time(8, 0),
            end_time=time(16, 0),
            paid_hours=8.0,
            assignment_mode=Shift.ASSIGNMENT_OPEN,
            live_version=0,
        )
        shift_b = Shift(
            schedule_week_id=week_b.id,
            position_id=position_b.id,
            shift_date=date(2026, 4, 7),
            start_time=time(9, 0),
            end_time=time(17, 0),
            paid_hours=8.0,
            assignment_mode=Shift.ASSIGNMENT_OPEN,
            live_version=0,
        )
        db.session.add_all([shift_a, shift_b])
        db.session.commit()

        week_a_id = week_a.id
        week_b_id = week_b.id
        shift_a_id = shift_a.id
        shift_b_id = shift_b.id

    with client:
        login(client, "bulk-publish@example.com", "pass")
        response = client.get(
            "/schedules?department_id=all&week_start=2026-04-06",
            follow_redirects=True,
        )
        assert response.status_code == 200
        assert b"Publish Allowed Departments" in response.data

        response = client.post(
            "/schedules?department_id=all&week_start=2026-04-06",
            data={
                "action": "publish_week",
                "department_id": "all",
                "week_start": "2026-04-06",
            },
            follow_redirects=True,
        )
        assert response.status_code == 200
        assert b"Published 1 department schedule week: Bulk Publish A." in response.data

    with app.app_context():
        week_a = db.session.get(DepartmentScheduleWeek, week_a_id)
        week_b = db.session.get(DepartmentScheduleWeek, week_b_id)
        shift_a = db.session.get(Shift, shift_a_id)
        shift_b = db.session.get(Shift, shift_b_id)

        assert week_a is not None
        assert week_b is not None
        assert shift_a is not None
        assert shift_b is not None

        assert week_a.is_published is True
        assert week_a.current_version == 1
        assert shift_a.live_version == 1

        assert week_b.is_published is False
        assert week_b.current_version == 0
        assert shift_b.live_version == 0

        flush_activity_logs()
        activities = [
            row.activity for row in ActivityLog.query.order_by(ActivityLog.id).all()
        ]
        assert any(
            "Published 1 department schedule week(s) for week 2026-04-06" in item
            for item in activities
        )


def test_all_departments_publish_requires_managed_departments(client, app):
    scheduler_id = create_user(app, "bulk-publish-no-scope@example.com")

    with app.app_context():
        scheduler = db.session.get(User, scheduler_id)
        grant_permissions(
            scheduler,
            "schedules.view_team",
            "schedules.publish",
            group_name="Bulk Publish No Scope",
            description="Can view schedules without managed publish scope.",
        )

        department_a = Department(name="Publish Scope A", active=True)
        department_b = Department(name="Publish Scope B", active=True)
        db.session.add_all([department_a, department_b])
        db.session.flush()

        db.session.add_all(
            [
                UserDepartmentMembership(
                    user_id=scheduler_id,
                    department_id=department_a.id,
                    role=UserDepartmentMembership.ROLE_STAFF,
                    is_primary=True,
                ),
                UserDepartmentMembership(
                    user_id=scheduler_id,
                    department_id=department_b.id,
                    role=UserDepartmentMembership.ROLE_STAFF,
                ),
                DepartmentScheduleWeek(
                    department_id=department_a.id,
                    week_start=date(2026, 4, 6),
                    is_published=False,
                    current_version=0,
                ),
                DepartmentScheduleWeek(
                    department_id=department_b.id,
                    week_start=date(2026, 4, 6),
                    is_published=False,
                    current_version=0,
                ),
            ]
        )
        db.session.commit()

    with client:
        login(client, "bulk-publish-no-scope@example.com", "pass")
        response = client.get(
            "/schedules?department_id=all&week_start=2026-04-06",
            follow_redirects=True,
        )
        assert response.status_code == 200
        assert b"Publish Allowed Departments" not in response.data

        response = client.post(
            "/schedules?department_id=all&week_start=2026-04-06",
            data={
                "action": "publish_week",
                "department_id": "all",
                "week_start": "2026-04-06",
            },
        )
        assert response.status_code == 403


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


def test_schedule_templates_can_create_add_entries_and_apply_to_draft_schedule(
    client,
    app,
):
    admin_email = os.getenv("ADMIN_EMAIL", "admin@example.com")
    admin_pass = os.getenv("ADMIN_PASS", "adminpass")

    with app.app_context():
        worker = User(
            email="template-worker@example.com",
            password=generate_password_hash("pass"),
            active=True,
        )
        department = Department(name="Template Warehouse", active=True)
        db.session.add_all([worker, department])
        db.session.flush()

        position = ShiftPosition(
            department_id=department.id,
            name="Runner",
            active=True,
        )
        db.session.add(position)
        db.session.flush()

        db.session.add(
            UserDepartmentMembership(
                user_id=worker.id,
                department_id=department.id,
                role="staff",
                is_primary=True,
            )
        )
        db.session.add(
            UserPositionEligibility(
                user_id=worker.id,
                position_id=position.id,
                priority=5,
                active=True,
            )
        )
        db.session.commit()
        worker_id = worker.id
        department_id = department.id
        position_id = position.id

    with client:
        login(client, admin_email, admin_pass)
        response = client.post(
            "/schedules/templates",
            data={
                "action": "create_template",
                "template-name": "Warehouse Runner Week",
                "template-description": "Reusable runner staffing",
                "template-department_id": str(department_id),
                "template-position_id": str(position_id),
                "template-span": "week",
                "template-active": "y",
            },
            follow_redirects=True,
        )
        assert response.status_code == 200
        assert b"Schedule template created." in response.data

    with app.app_context():
        template = ScheduleTemplate.query.filter_by(name="Warehouse Runner Week").first()
        assert template is not None
        template_id = template.id

    with client:
        login(client, admin_email, admin_pass)
        response = client.post(
            f"/schedules/templates/{template_id}",
            data={
                "action": "save_entry",
                "entry-weekday": "0",
                "entry-assignment_mode": "assigned",
                "entry-assigned_user_id": str(worker_id),
                "entry-start_time": "09:00",
                "entry-end_time": "17:00",
                "entry-paid_hours": "8",
                "entry-paid_hours_manual": "y",
                "entry-notes": "Opening runner",
            },
            follow_redirects=True,
        )
        assert response.status_code == 200
        assert b"Template shift saved." in response.data

        response = client.post(
            f"/schedules/templates/{template_id}",
            data={
                "action": "save_entry",
                "entry-weekday": "2",
                "entry-assignment_mode": "open",
                "entry-assigned_user_id": "0",
                "entry-start_time": "12:00",
                "entry-end_time": "16:00",
                "entry-notes": "Midweek coverage",
            },
            follow_redirects=True,
        )
        assert response.status_code == 200
        assert b"Template shift saved." in response.data

        response = client.post(
            "/schedules/templates",
            data={
                "action": "apply_templates",
                "apply-target_start_date": "2026-05-04",
                "template_ids": str(template_id),
            },
            follow_redirects=True,
        )
        assert response.status_code == 200
        assert b"Applied 1 template(s) and created 2 shift(s)." in response.data

    with app.app_context():
        week = DepartmentScheduleWeek.query.filter_by(
            department_id=department_id,
            week_start=date(2026, 5, 4),
        ).first()
        assert week is not None
        assert week.is_published is False

        shifts = (
            Shift.query.filter_by(schedule_week_id=week.id)
            .order_by(Shift.shift_date.asc(), Shift.start_time.asc())
            .all()
        )
        assert len(shifts) == 2
        assert [shift.shift_date for shift in shifts] == [
            date(2026, 5, 4),
            date(2026, 5, 6),
        ]
        assert shifts[0].assigned_user_id == worker_id
        assert shifts[0].assignment_mode == Shift.ASSIGNMENT_ASSIGNED
        assert shifts[0].paid_hours == 8.0
        assert shifts[1].assigned_user_id is None
        assert shifts[1].assignment_mode == Shift.ASSIGNMENT_OPEN

        flush_activity_logs()
        activities = [
            row.activity for row in ActivityLog.query.order_by(ActivityLog.id).all()
        ]
        assert any(
            "Created schedule template Warehouse Runner Week" in item
            for item in activities
        )
        assert any(
            "Added schedule template shift on Warehouse Runner Week" in item
            for item in activities
        )
        assert any(
            "Applied 1 schedule template(s) creating 2 shift(s)" in item
            for item in activities
        )


def test_schedule_templates_apply_blocks_published_weeks(client, app):
    admin_email = os.getenv("ADMIN_EMAIL", "admin@example.com")
    admin_pass = os.getenv("ADMIN_PASS", "adminpass")

    with app.app_context():
        department = Department(name="Published Template Dept", active=True)
        db.session.add(department)
        db.session.flush()
        position = ShiftPosition(
            department_id=department.id,
            name="Cashier",
            active=True,
        )
        db.session.add(position)
        db.session.flush()

        template = ScheduleTemplate(
            name="Published Week Template",
            department_id=department.id,
            position_id=position.id,
            span=ScheduleTemplate.SPAN_WEEK,
            active=True,
        )
        db.session.add(template)
        db.session.flush()
        db.session.add(
            ScheduleTemplateEntry(
                template_id=template.id,
                weekday=0,
                assignment_mode=Shift.ASSIGNMENT_OPEN,
                start_time=time(10, 0),
                end_time=time(14, 0),
                paid_hours=4.0,
                paid_hours_manual=True,
            )
        )
        db.session.add(
            DepartmentScheduleWeek(
                department_id=department.id,
                week_start=date(2026, 6, 1),
                is_published=True,
                current_version=1,
            )
        )
        db.session.commit()
        template_id = template.id
        department_id = department.id

    with client:
        login(client, admin_email, admin_pass)
        response = client.post(
            "/schedules/templates",
            data={
                "action": "apply_templates",
                "apply-target_start_date": "2026-06-03",
                "template_ids": str(template_id),
            },
            follow_redirects=True,
        )
        assert response.status_code == 200
        assert b"Templates can only be applied to draft schedule weeks." in response.data

    with app.app_context():
        week = DepartmentScheduleWeek.query.filter_by(
            department_id=department_id,
            week_start=date(2026, 6, 1),
        ).first()
        assert week is not None
        assert Shift.query.filter_by(schedule_week_id=week.id).count() == 0


def test_schedule_templates_support_month_and_year_period_application(client, app):
    admin_email = os.getenv("ADMIN_EMAIL", "admin@example.com")
    admin_pass = os.getenv("ADMIN_PASS", "adminpass")

    with app.app_context():
        department = Department(name="Template Calendar Dept", active=True)
        db.session.add(department)
        db.session.flush()
        position = ShiftPosition(
            department_id=department.id,
            name="Bartender",
            active=True,
        )
        db.session.add(position)
        db.session.flush()

        month_template = ScheduleTemplate(
            name="Month Midpoint",
            department_id=department.id,
            position_id=position.id,
            span=ScheduleTemplate.SPAN_MONTH,
            active=True,
        )
        year_template = ScheduleTemplate(
            name="Year Holiday",
            department_id=department.id,
            position_id=position.id,
            span=ScheduleTemplate.SPAN_YEAR,
            active=True,
        )
        db.session.add_all([month_template, year_template])
        db.session.flush()

        db.session.add(
            ScheduleTemplateEntry(
                template_id=month_template.id,
                day_of_month=15,
                assignment_mode=Shift.ASSIGNMENT_OPEN,
                start_time=time(11, 0),
                end_time=time(15, 0),
                paid_hours=4.0,
                paid_hours_manual=True,
            )
        )
        db.session.add(
            ScheduleTemplateEntry(
                template_id=year_template.id,
                month_of_year=7,
                day_of_month=4,
                assignment_mode=Shift.ASSIGNMENT_TRADEBOARD,
                start_time=time(17, 0),
                end_time=time(23, 0),
                paid_hours=6.0,
                paid_hours_manual=True,
            )
        )
        db.session.commit()
        month_template_id = month_template.id
        year_template_id = year_template.id

    with client:
        login(client, admin_email, admin_pass)
        response = client.post(
            "/schedules/templates",
            data={
                "action": "apply_templates",
                "apply-target_start_date": "2027-03-09",
                "template_ids": [str(month_template_id), str(year_template_id)],
            },
            follow_redirects=True,
        )
        assert response.status_code == 200
        assert b"Applied 2 template(s) and created 2 shift(s)." in response.data

    with app.app_context():
        march_shift = Shift.query.filter_by(shift_date=date(2027, 3, 15)).first()
        july_shift = Shift.query.filter_by(shift_date=date(2027, 7, 4)).first()
        assert march_shift is not None
        assert march_shift.assignment_mode == Shift.ASSIGNMENT_OPEN
        assert july_shift is not None
        assert july_shift.assignment_mode == Shift.ASSIGNMENT_TRADEBOARD


def test_schedule_templates_are_scoped_to_managed_departments(client, app):
    manager_id = create_user(app, "template-manager@example.com")

    with app.app_context():
        manager = db.session.get(User, manager_id)
        grant_permissions(
            manager,
            "schedules.manage_templates",
            "schedules.apply_templates",
            group_name="Template Manager Permissions",
            description="Can manage and apply schedule templates.",
        )

        department_a = Department(name="Managed Template Dept", active=True)
        department_b = Department(name="Hidden Template Dept", active=True)
        db.session.add_all([department_a, department_b])
        db.session.flush()

        position_a = ShiftPosition(
            department_id=department_a.id,
            name="Manager Position",
            active=True,
        )
        position_b = ShiftPosition(
            department_id=department_b.id,
            name="Hidden Position",
            active=True,
        )
        db.session.add_all([position_a, position_b])
        db.session.flush()

        db.session.add(
            UserDepartmentMembership(
                user_id=manager.id,
                department_id=department_a.id,
                role=UserDepartmentMembership.ROLE_MANAGER,
                is_primary=True,
            )
        )
        db.session.add_all(
            [
                ScheduleTemplate(
                    name="Visible Template",
                    department_id=department_a.id,
                    position_id=position_a.id,
                    span=ScheduleTemplate.SPAN_WEEK,
                    active=True,
                ),
                ScheduleTemplate(
                    name="Hidden Template",
                    department_id=department_b.id,
                    position_id=position_b.id,
                    span=ScheduleTemplate.SPAN_WEEK,
                    active=True,
                ),
            ]
        )
        db.session.commit()
        hidden_template = ScheduleTemplate.query.filter_by(
            name="Hidden Template"
        ).first()
        assert hidden_template is not None
        hidden_template_id = hidden_template.id

    with client:
        login(client, "template-manager@example.com", "pass")
        response = client.get("/schedules/templates", follow_redirects=True)
        assert response.status_code == 200
        assert b"Visible Template" in response.data
        assert b"Hidden Template" not in response.data

        detail_response = client.get(
            f"/schedules/templates/{hidden_template_id}",
            follow_redirects=False,
        )
        assert detail_response.status_code == 404
