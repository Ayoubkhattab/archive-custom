#!/bin/bash
# =============================================================================
# Paperless-ngx Restore Script
# =============================================================================
# Usage: ./restore.sh <backup-file.tar.gz>
#
# Restores a backup created by backup.sh:
#   - Stops background workers (celery, celery-beat)
#   - Restores PostgreSQL database from dump
#   - Restores documents via Paperless-ngx importer
#   - Runs migrations and sanity check
#   - Restarts all services
#
# Run from the docker/compose/ directory where docker-compose.dev.yml is located.
# =============================================================================

set -euo pipefail

COMPOSE_FILE="docker-compose.dev.yml"

# Validate arguments
if [ $# -lt 1 ]; then
    echo "Usage: $0 <backup-file.tar.gz>"
    echo ""
    echo "Example: $0 ./backups/paperless-backup-20260206_120000.tar.gz"
    exit 1
fi

BACKUP_FILE="$1"

if [ ! -f "${BACKUP_FILE}" ]; then
    echo "ERROR: Backup file not found: ${BACKUP_FILE}"
    exit 1
fi

# Verify checksum if available
CHECKSUM_FILE="${BACKUP_FILE}.sha256"
if [ -f "${CHECKSUM_FILE}" ]; then
    echo "Verifying backup checksum..."
    if sha256sum -c "${CHECKSUM_FILE}" > /dev/null 2>&1; then
        echo "Checksum OK"
    else
        echo "ERROR: Checksum verification failed!"
        echo "The backup file may be corrupted."
        exit 1
    fi
fi

echo ""
echo "=============================================="
echo "  Paperless-ngx Restore"
echo "=============================================="
echo "  Backup: ${BACKUP_FILE}"
echo "  Size: $(du -sh "${BACKUP_FILE}" | cut -f1)"
echo "=============================================="
echo ""
echo "WARNING: This will REPLACE all current data!"
echo "         Make sure you have a backup of the current state."
echo ""
read -r -p "Continue? (y/N): " confirm
if [ "${confirm}" != "y" ] && [ "${confirm}" != "Y" ]; then
    echo "Aborted."
    exit 0
fi

# Create temp directory
RESTORE_DIR=$(mktemp -d)
trap "rm -rf ${RESTORE_DIR}" EXIT

echo ""
echo "[1/7] Extracting backup archive..."
tar -xzf "${BACKUP_FILE}" -C "${RESTORE_DIR}"

# Find files in the extracted archive
DB_DUMP=$(find "${RESTORE_DIR}" -name "*_db.dump" -type f | head -1)
DOCS_ZIP=$(find "${RESTORE_DIR}" -name "*_docs.zip" -type f | head -1)

echo "      Database dump: $([ -n "${DB_DUMP}" ] && echo "Found" || echo "Not found")"
echo "      Documents ZIP: $([ -n "${DOCS_ZIP}" ] && echo "Found" || echo "Not found")"

echo ""
echo "[2/7] Stopping background workers..."
docker compose -f "${COMPOSE_FILE}" stop celery celery-beat 2>/dev/null || true

echo ""
echo "[3/7] Restoring PostgreSQL database..."
if [ -n "${DB_DUMP}" ]; then
    # Drop and recreate database
    docker compose -f "${COMPOSE_FILE}" exec -T postgres \
        psql -U paperless -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname='paperless' AND pid <> pg_backend_pid();" postgres 2>/dev/null || true

    docker compose -f "${COMPOSE_FILE}" exec -T postgres \
        pg_restore -U paperless -d paperless --clean --if-exists < "${DB_DUMP}" 2>/dev/null || true

    echo "      Database restored"
else
    echo "      WARNING: No database dump found, skipping database restore"
fi

echo ""
echo "[4/7] Restoring documents..."
if [ -n "${DOCS_ZIP}" ]; then
    # Copy ZIP to container-accessible location
    CONTAINER_ZIP="/usr/src/paperless/export/restore_docs.zip"
    docker compose -f "${COMPOSE_FILE}" cp "${DOCS_ZIP}" "webserver:${CONTAINER_ZIP}"

    docker compose -f "${COMPOSE_FILE}" exec -T webserver \
        python /usr/src/paperless/src/manage.py document_importer "${CONTAINER_ZIP}" \
        --no-progress-bar 2>/dev/null || true

    # Cleanup
    docker compose -f "${COMPOSE_FILE}" exec -T webserver \
        rm -f "${CONTAINER_ZIP}" 2>/dev/null || true

    echo "      Documents restored"
else
    echo "      WARNING: No document export found, skipping document restore"
fi

echo ""
echo "[5/7] Running database migrations..."
docker compose -f "${COMPOSE_FILE}" exec -T webserver \
    python /usr/src/paperless/src/manage.py migrate --noinput

echo ""
echo "[6/7] Restarting background workers..."
docker compose -f "${COMPOSE_FILE}" start celery celery-beat

echo ""
echo "[7/7] Running sanity check..."
docker compose -f "${COMPOSE_FILE}" exec -T webserver \
    python /usr/src/paperless/src/manage.py document_sanity_checker --no-progress-bar 2>/dev/null || true

echo ""
echo "=============================================="
echo "  Restore Complete!"
echo "=============================================="
echo "  You may need to rebuild the search index:"
echo "  docker compose -f ${COMPOSE_FILE} exec webserver \\"
echo "    python /usr/src/paperless/src/manage.py document_index --reindex"
echo "=============================================="
