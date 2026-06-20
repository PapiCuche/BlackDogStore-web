# Python 3.12 — compatible with Django 5.2 LTS and Django 6.x (easy future upgrade)
# EOL October 2028, outlasts Django 5.2 LTS (April 2028)
FROM python:3.12-slim

WORKDIR /app

# Prevent .pyc files and enable unbuffered logs
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Install system dependencies required by psycopg2-binary and Pillow
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies first (layer cached unless requirements.txt changes)
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY . /app

# Collect static files for Django admin (requires SECRET_KEY at build time or use placeholder)
# Run this before starting the server in entrypoint instead

# Development entrypoint — replace with gunicorn for production
# Production: gunicorn backend.wsgi:application --bind 0.0.0.0:8000 --workers 4
CMD ["sh", "-c", "python manage.py migrate --noinput && python manage.py runserver 0.0.0.0:8000"]
