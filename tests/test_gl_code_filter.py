from werkzeug.security import generate_password_hash

from app import db
from app.models import GLCode, User
from tests.utils import login

def setup_data(app):
    with app.app_context():
        user = User(
            email="glfilter@example.com",
            password=generate_password_hash("pass"),
            active=True,
        )
        gl1 = GLCode(code="1000", description="Food")
        gl2 = GLCode(code="2000", description="Drink")
        db.session.add_all([user, gl1, gl2])
        db.session.commit()
        return user.email


def test_view_gl_codes_filter_by_code(client, app):
    email = setup_data(app)
    with client:
        login(client, email, "pass")
        resp = client.get("/gl_codes?code_query=100")
        assert resp.status_code == 200
        assert b"1000" in resp.data
        assert b"2000" not in resp.data
