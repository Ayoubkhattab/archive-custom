#!/bin/bash
set -e

echo "Starting Paperless-ngx Production Environment..."

cd /usr/src/paperless/src

# Wait for database
echo "Waiting for database..."
until pg_isready -h "${PAPERLESS_DBHOST:-postgres}" -U "${PAPERLESS_DBUSER:-paperless}" > /dev/null 2>&1; do
  sleep 2
done
echo "Database is ready."

# Wait for Redis
echo "Waiting for Redis..."
until python -c "
import redis, os
url = os.environ.get('PAPERLESS_REDIS', 'redis://broker:6379/0')
r = redis.Redis.from_url(url)
r.ping()
" 2>/dev/null; do
  sleep 2
done
echo "Redis is ready."

# Run database migrations
echo "Running migrations..."
python manage.py migrate --noinput

# Create superuser if PAPERLESS_ADMIN_USER is set and doesn't exist yet
python manage.py shell << 'PYEOF'
import os
from django.contrib.auth import get_user_model
User = get_user_model()
username = os.environ.get('PAPERLESS_ADMIN_USER')
password = os.environ.get('PAPERLESS_ADMIN_PASSWORD')
if username and password:
    if not User.objects.filter(username=username).exists():
        User.objects.create_superuser(username, '', password)
        print(f"Superuser '{username}' created.")
    else:
        print(f"Superuser '{username}' already exists.")
PYEOF

# Set up document structure (idempotent - safe to run every time)
echo "Setting up document structure..."
python manage.py setup_document_structure

# Static files are collected and compressed at image build time. Only redo it
# if PAPERLESS_STATICDIR points somewhere empty.
if [ ! -f "${PAPERLESS_STATICDIR:-/usr/src/paperless/static}/frontend/en-US/index.html" ]; then
  echo "Collecting static files..."
  python manage.py collectstatic --noinput
fi

# Start production server (GRANIAN_WORKERS workers, default 4; no reload).
# asginl: Django has no ASGI lifespan support, so "asgi" logs a lifespan error
# on every worker start. Same flags as the upstream image's s6 service.
echo "Starting Granian ASGI server..."
exec granian \
  --interface asginl \
  --ws \
  --loop uvloop \
  --respawn-failed-workers \
  --host 0.0.0.0 \
  --port 8000 \
  --workers "${GRANIAN_WORKERS:-4}" \
  paperless.asgi:application
