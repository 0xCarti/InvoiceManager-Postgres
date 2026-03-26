FROM python:3.11-slim

# Ensure predictable Python behavior
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Set working directory
WORKDIR /app

# Install native dependencies required by WeasyPrint
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        libcairo2 \
        libgdk-pixbuf-2.0-0 \
        libgobject-2.0-0 \
        libglib2.0-0 \
        libpango-1.0-0 \
        libpangocairo-1.0-0 \
        fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Create and use a non-root user
RUN groupadd -r app && useradd -r -g app app

# Copy application code
COPY . .

RUN mkdir -p /app/data /app/uploads /app/backups \
    && chown -R app:app /app \
    && chmod +x /app/entrypoint.sh

ARG PORT=5000
ENV PORT=${PORT}
ENV FLASK_APP=run.py
ENV FLASK_SKIP_CREATE_ALL=1
ENV DATABASE_PATH=/app/data/inventory.db

EXPOSE ${PORT}

USER app

ENTRYPOINT ["./entrypoint.sh"]
CMD ["gunicorn", "-c", "gunicorn.conf.py", "run:app"]
