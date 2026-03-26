#!/usr/bin/env bash
set -euo pipefail

REPO_URL=${1:-"https://github.com/0xCarti/InvoiceManager.git"}
TARGET_DIR=${2:-"InvoiceManager"}

if ! command -v git >/dev/null 2>&1; then
    echo "git is required but not installed. Please install git." >&2
    exit 1
fi

if [ ! -d "$TARGET_DIR" ]; then
    echo "Cloning $REPO_URL into $TARGET_DIR"
    git clone "$REPO_URL" "$TARGET_DIR"
else
    echo "Directory $TARGET_DIR exists. Pulling latest changes."
    git -C "$TARGET_DIR" pull
fi

cd "$TARGET_DIR"

PYTHON_CMD=""
if command -v python3 >/dev/null 2>&1; then
    PYTHON_CMD="python3"
elif command -v python >/dev/null 2>&1; then
    PYTHON_CMD="python"
else
    echo "Python 3 is required but not installed. Please install Python 3." >&2
    exit 1
fi

"$PYTHON_CMD" -m venv venv
if [ ! -f "venv/bin/activate" ]; then
    echo "Failed to create virtual environment." >&2
    exit 1
fi

# shellcheck disable=SC1091
source venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt

if [ ! -f .env ]; then
    if [ -f .env.example ]; then
        cp .env.example .env
        echo "Created .env from example. Edit it with your settings."
    else
        echo "Warning: .env.example not found; please create a .env file manually." >&2
    fi
fi

echo "Running database migrations..."
python -m flask --app run.py db upgrade

echo "Seeding initial data..."
python seed_data.py

echo "Setup complete. To start the application run:\nsource venv/bin/activate && python run.py"
