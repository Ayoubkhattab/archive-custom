#!/bin/bash
set -e

echo "🚀 Starting Paperless-ngx Development Environment..."

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

# Run migrations
echo "🔄 Running database migrations..."
python manage.py migrate --noinput

# Create superuser if it doesn't exist
echo "👤 Creating superuser..."
python manage.py shell << 'EOF'
import os
from django.contrib.auth import get_user_model
User = get_user_model()
username = os.environ.get('PAPERLESS_ADMIN_USER', 'admin')
email = os.environ.get('PAPERLESS_ADMIN_EMAIL', 'admin@example.com')
password = os.environ.get('PAPERLESS_ADMIN_PASSWORD', 'admin123')
if not User.objects.filter(username=username).exists():
    User.objects.create_superuser(username, email, password)
    print(f'✅ Superuser "{username}" created successfully!')
else:
    print(f'✅ Superuser "{username}" already exists')
EOF

# Set up document structure (idempotent)
echo "📋 Setting up document structure..."
python manage.py setup_document_structure

# Collect static files
echo "📦 Collecting static files..."
python manage.py collectstatic --noinput --clear

# Start development server with hot reload
echo "🔥 Starting development server with hot reload..."
echo "📝 Environment: $PAPERLESS_ENV"
echo "🐛 Debug mode: $DEBUG"

# Use granian (ASGI server) for WebSocket support
# Django's runserver does NOT support WebSockets!
echo "🔥 Starting Granian ASGI server (WebSocket enabled)..."
exec granian --interface asgi --host 0.0.0.0 --port 8000 --reload paperless.asgi:application
