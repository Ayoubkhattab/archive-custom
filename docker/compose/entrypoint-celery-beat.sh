#!/bin/bash
set -e

echo "🚀 Starting Paperless Celery Beat..."

# Change to source directory
cd /usr/src/paperless/src

# Wait for database
echo "⏳ Waiting for database..."
until pg_isready -h postgres -U paperless -p 5432 > /dev/null 2>&1; do
  echo "Database not ready yet, waiting..."
  sleep 1
done
echo "✅ Database is ready!"

# Wait for Redis
echo "⏳ Waiting for Redis..."
until python -c "import redis; r=redis.Redis(host='broker', port=6379); r.ping()" 2>/dev/null; do
  echo "Redis not ready yet, waiting..."
  sleep 1
done
echo "✅ Redis is ready!"

# Start Celery beat
echo "🔥 Starting Celery beat..."
exec celery -A paperless beat --loglevel=info
