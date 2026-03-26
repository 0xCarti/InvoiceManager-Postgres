#!/bin/sh
set -e

flask db upgrade

# Ensure the database has the initial admin user and settings
python <<'PYTHON'
from seed_data import seed_initial_data
from app import create_app
from app.models import User, Setting

app, _ = create_app([])
with app.app_context():
    needs_seed = (
        User.query.filter_by(is_admin=True).first() is None
        or Setting.query.filter_by(name="GST").first() is None
        or Setting.query.filter_by(name="DEFAULT_TIMEZONE").first() is None
    )
    if needs_seed:
        seed_initial_data()
PYTHON

if [ "$1" = "gunicorn" ]; then
    shift
    exec gunicorn "$@"
fi

exec "$@"
