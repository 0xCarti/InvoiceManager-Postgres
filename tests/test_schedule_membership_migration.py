import json

from flask_migrate import downgrade, upgrade
from sqlalchemy import inspect, text
from werkzeug.security import generate_password_hash

from app import db
from app.models import Department, User


PREVIOUS_REVISION = "5e6f7a8b9c0d"
ACCESS_REVISION = "6f7a8b9c0d1e"


def test_schedule_membership_access_migration_preserves_legacy_access(app):
    with app.app_context():
        users = {
            role_name: User(
                email=f"migration-{role_name.replace(' ', '-')}@example.com",
                password=generate_password_hash("pass"),
                active=True,
            )
            for role_name in ("staff", "manager", "gm", "shift lead", "coordinator")
        }
        departments = [
            Department(name="Migration Department A", active=True),
            Department(name="Migration Department B", active=True),
        ]
        db.session.add_all([*users.values(), *departments])
        db.session.commit()
        user_ids = {
            role_name: user.id for role_name, user in users.items()
        }
        department_ids = [department.id for department in departments]
        db.session.remove()

        downgrade(revision=PREVIOUS_REVISION)

        role_catalog = json.dumps(
            [
                {"name": "manager", "is_management": False},
                {"name": "gm", "is_management": True},
                {"name": "shift lead", "is_management": True},
                {"name": "coordinator", "is_management": False},
            ]
        )
        with db.engine.begin() as connection:
            connection.execute(
                text(
                    "DELETE FROM user_permission_groups "
                    "WHERE permission_group_id IN ("
                    "SELECT id FROM permission_group "
                    "WHERE key = 'legacy_schedule_gm_global_scope'"
                    ")"
                )
            )
            connection.execute(
                text(
                    "DELETE FROM permission_group_permissions "
                    "WHERE permission_group_id IN ("
                    "SELECT id FROM permission_group "
                    "WHERE key = 'legacy_schedule_gm_global_scope'"
                    ") OR permission_id IN ("
                    "SELECT id FROM permission "
                    "WHERE code = 'communications.global_scope'"
                    ")"
                )
            )
            connection.execute(
                text(
                    "DELETE FROM permission_group "
                    "WHERE key = 'legacy_schedule_gm_global_scope'"
                )
            )
            connection.execute(
                text(
                    "DELETE FROM permission "
                    "WHERE code = 'communications.global_scope'"
                )
            )
            connection.execute(
                text(
                    "DELETE FROM setting "
                    "WHERE name = 'SCHEDULE_MEMBERSHIP_ROLES'"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO setting (name, value) "
                    "VALUES ('SCHEDULE_MEMBERSHIP_ROLES', :value)"
                ),
                {"value": role_catalog},
            )
            connection.execute(
                text(
                    "INSERT INTO schedule_user_department_membership "
                    "(user_id, department_id, role) VALUES "
                    "(:staff, :department_a, 'staff'), "
                    "(:manager, :department_a, 'manager'), "
                    "(:gm, :department_a, 'GM'), "
                    "(:gm, :department_b, 'gm'), "
                    "(:shift_lead, :department_a, '  Shift   Lead  '), "
                    "(:coordinator, :department_a, 'coordinator')"
                ),
                {
                    "staff": user_ids["staff"],
                    "manager": user_ids["manager"],
                    "gm": user_ids["gm"],
                    "shift_lead": user_ids["shift lead"],
                    "coordinator": user_ids["coordinator"],
                    "department_a": department_ids[0],
                    "department_b": department_ids[1],
                },
            )

        upgrade(revision=ACCESS_REVISION)
        db.session.remove()

        with db.engine.connect() as connection:
            access_rows = connection.execute(
                text(
                    "SELECT user_id, can_manage_department "
                    "FROM schedule_user_department_membership"
                )
            ).all()
            access_by_user_id: dict[int, set[bool]] = {}
            for user_id, can_manage_department in access_rows:
                if user_id not in user_ids.values():
                    continue
                access_by_user_id.setdefault(int(user_id), set()).add(
                    bool(can_manage_department)
                )

            assert access_by_user_id == {
                user_ids["staff"]: {False},
                user_ids["manager"]: {False},
                user_ids["gm"]: {True},
                user_ids["shift lead"]: {True},
                user_ids["coordinator"]: {False},
            }

            global_scope_user_ids = {
                int(row.user_id)
                for row in connection.execute(
                    text(
                        "SELECT upg.user_id "
                        "FROM user_permission_groups AS upg "
                        "JOIN permission_group AS pg "
                        "ON pg.id = upg.permission_group_id "
                        "JOIN permission_group_permissions AS pgp "
                        "ON pgp.permission_group_id = pg.id "
                        "JOIN permission AS p ON p.id = pgp.permission_id "
                        "WHERE pg.key = 'legacy_schedule_gm_global_scope' "
                        "AND p.code = 'communications.global_scope'"
                    )
                )
            }
            assert global_scope_user_ids == {user_ids["gm"]}

        membership_columns = {
            column["name"]
            for column in inspect(db.engine).get_columns(
                "schedule_user_department_membership"
            )
        }
        assert "can_manage_department" in membership_columns
        assert "role" in membership_columns

        with db.engine.begin() as connection:
            legacy_group_id = connection.execute(
                text(
                    "SELECT id FROM permission_group "
                    "WHERE key = 'legacy_schedule_gm_global_scope'"
                )
            ).scalar_one()
            connection.execute(
                text(
                    "INSERT INTO user_permission_groups "
                    "(user_id, permission_group_id) "
                    "VALUES (:user_id, :permission_group_id)"
                ),
                {
                    "user_id": user_ids["staff"],
                    "permission_group_id": legacy_group_id,
                },
            )

        db.session.remove()
        downgrade(revision=PREVIOUS_REVISION)

        with db.engine.connect() as connection:
            preserved_user_ids = {
                int(row.user_id)
                for row in connection.execute(
                    text(
                        "SELECT upg.user_id "
                        "FROM user_permission_groups AS upg "
                        "JOIN permission_group AS pg "
                        "ON pg.id = upg.permission_group_id "
                        "JOIN permission_group_permissions AS pgp "
                        "ON pgp.permission_group_id = pg.id "
                        "JOIN permission AS p ON p.id = pgp.permission_id "
                        "WHERE pg.key = 'legacy_schedule_gm_global_scope' "
                        "AND p.code = 'communications.global_scope'"
                    )
                )
            }
            assert preserved_user_ids == {
                user_ids["gm"],
                user_ids["staff"],
            }

        downgraded_columns = {
            column["name"]
            for column in inspect(db.engine).get_columns(
                "schedule_user_department_membership"
            )
        }
        assert "can_manage_department" not in downgraded_columns
        assert "role" in downgraded_columns

        upgrade(revision=ACCESS_REVISION)


def test_schedule_membership_access_migration_defaults_manager_to_true(app):
    with app.app_context():
        user = User(
            email="migration-default-manager@example.com",
            password=generate_password_hash("pass"),
            active=True,
        )
        department = Department(name="Migration Default Manager", active=True)
        db.session.add_all([user, department])
        db.session.commit()
        user_id = user.id
        department_id = department.id
        db.session.remove()

        downgrade(revision=PREVIOUS_REVISION)
        with db.engine.begin() as connection:
            connection.execute(
                text(
                    "DELETE FROM setting "
                    "WHERE name = 'SCHEDULE_MEMBERSHIP_ROLES'"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO schedule_user_department_membership "
                    "(user_id, department_id, role) "
                    "VALUES (:user_id, :department_id, 'manager')"
                ),
                {"user_id": user_id, "department_id": department_id},
            )

        upgrade(revision=ACCESS_REVISION)
        db.session.remove()

        can_manage_department = db.session.execute(
            text(
                "SELECT can_manage_department "
                "FROM schedule_user_department_membership "
                "WHERE user_id = :user_id AND department_id = :department_id"
            ),
            {"user_id": user_id, "department_id": department_id},
        ).scalar_one()
        assert bool(can_manage_department) is True
