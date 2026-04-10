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
                "membership-role": "staff",
                "membership-reports_to_user_id": "0",
                "membership-is_primary": "y",
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
        assert UserDepartmentMembership.query.filter_by(
            user_id=target_user_id, department_id=department_id
        ).first()
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
