from datetime import datetime

from flask_migrate import downgrade, upgrade
from sqlalchemy import text

from app import db
from app.models import User


def test_invitation_state_migration_backfills_only_legacy_pending_accounts(
    app,
):
    with app.app_context():
        db.session.add_all(
            [
                User(
                    email="legacy-pending@example.com",
                    password="hash",
                    active=False,
                    invitation_pending=False,
                ),
                User(
                    email="legacy-active@example.com",
                    password="hash",
                    active=True,
                    invitation_pending=False,
                ),
                User(
                    email="legacy-inactive@example.com",
                    password="hash",
                    active=False,
                    invitation_pending=False,
                    last_login_at=datetime.utcnow(),
                ),
            ]
        )
        db.session.commit()
        db.session.remove()
        downgrade(revision="6f7a8b9c0d1e")
        upgrade()

        with db.engine.connect() as connection:
            rows = dict(
                connection.execute(
                    text(
                        'SELECT email, invitation_pending FROM "user" '
                        "WHERE email LIKE 'legacy-%@example.com'"
                    )
                ).all()
            )

    assert rows == {
        "legacy-pending@example.com": True,
        "legacy-active@example.com": False,
        "legacy-inactive@example.com": False,
    }
