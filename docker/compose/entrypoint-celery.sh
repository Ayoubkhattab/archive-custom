#!/bin/bash
set -e

echo "Starting Paperless Celery Worker..."

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

# Same flags as the upstream image: mingle/gossip only add broker chatter and
# startup delay for a single worker node.
#
# Lower CPU priority than the AI model: OCR and Ollama share the host's cores,
# and at equal priority a batch of documents being processed makes every chat
# answer crawl. With nice 10 background work still uses every idle core but
# yields them the moment a question is being answered. (Raising niceness needs
# no privileges, so this works under no-new-privileges.)
exec nice -n "${CELERY_NICE:-10}"   celery -A paperless worker --loglevel=info --without-mingle --without-gossip
