#!/bin/bash
set -e

echo "Starting Paperless Celery Beat..."

cd /usr/src/paperless/src

echo "Waiting for database..."
until pg_isready -h "${PAPERLESS_DBHOST:-postgres}" -p "${PAPERLESS_DBPORT:-5432}" -U "${PAPERLESS_DBUSER:-paperless}" > /dev/null 2>&1; do
  sleep 1
done
echo "Database is ready."

echo "Waiting for Redis..."
until python -c "
import os, redis
redis.Redis.from_url(os.environ.get('PAPERLESS_REDIS', 'redis://broker:6379/0')).ping()
" 2>/dev/null; do
  sleep 1
done
echo "Redis is ready."

exec celery -A paperless beat --loglevel=info
