#!/bin/bash
# =============================================================================
# Paperless-ngx Backup Script
# =============================================================================
# Usage: ./backup.sh [--keep-days N]
#
# Creates a timestamped backup containing:
#   - PostgreSQL database dump (custom format)
#   - Document export via Paperless-ngx exporter (ZIP with checksums)
#
# Run from the docker/compose/ directory where docker-compose.dev.yml is located.
# =============================================================================

set -euo pipefail

# Configuration
COMPOSE_FILE="docker-compose.dev.yml"
BACKUP_DIR="${PAPERLESS_BACKUP_DIR:-./backups}"
KEEP_DAYS="${1:-30}"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
BACKUP_NAME="paperless-backup-${TIMESTAMP}"
TEMP_EXPORT_DIR="/usr/src/paperless/export/backup_${TIMESTAMP}"

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --keep-days)
            KEEP_DAYS="$2"
            shift 2
            ;;
        *)
            shift
            ;;
    esac
done

echo "=============================================="
echo "  Paperless-ngx Backup"
echo "  $(date)"
echo "=============================================="

# Ensure backup directory exists
mkdir -p "${BACKUP_DIR}"

# Step 1: Database dump
echo ""
echo "[1/4] Dumping PostgreSQL database..."
docker compose -f "${COMPOSE_FILE}" exec -T postgres \
    pg_dump -U paperless -Fc paperless > "${BACKUP_DIR}/${BACKUP_NAME}_db.dump"

DB_SIZE=$(du -sh "${BACKUP_DIR}/${BACKUP_NAME}_db.dump" | cut -f1)
echo "      Database dump: ${DB_SIZE}"

# Step 2: Document export
echo ""
echo "[2/4] Exporting documents..."
docker compose -f "${COMPOSE_FILE}" exec -T webserver \
    python /usr/src/paperless/src/manage.py document_exporter \
    "${TEMP_EXPORT_DIR}" \
    --zip \
    --compare-checksums \
    --use-folder-prefix \
    --no-progress-bar

# Copy the ZIP from the container export volume
EXPORT_ZIP=$(find ./export/backup_${TIMESTAMP} -name "*.zip" 2>/dev/null | head -1)
if [ -n "${EXPORT_ZIP}" ]; then
    cp "${EXPORT_ZIP}" "${BACKUP_DIR}/${BACKUP_NAME}_docs.zip"
    DOCS_SIZE=$(du -sh "${BACKUP_DIR}/${BACKUP_NAME}_docs.zip" | cut -f1)
    echo "      Document export: ${DOCS_SIZE}"
else
    echo "      Warning: No document export ZIP found (may be empty database)"
fi

# Step 3: Create combined archive
echo ""
echo "[3/4] Creating backup archive..."
cd "${BACKUP_DIR}"

# Build tar with available files
TAR_FILES="${BACKUP_NAME}_db.dump"
if [ -f "${BACKUP_NAME}_docs.zip" ]; then
    TAR_FILES="${TAR_FILES} ${BACKUP_NAME}_docs.zip"
fi

tar -czf "${BACKUP_NAME}.tar.gz" ${TAR_FILES}

# Generate checksum
sha256sum "${BACKUP_NAME}.tar.gz" > "${BACKUP_NAME}.tar.gz.sha256"

# Cleanup temporary files
rm -f "${BACKUP_NAME}_db.dump"
rm -f "${BACKUP_NAME}_docs.zip"
cd - > /dev/null

# Cleanup export directory in container
rm -rf "./export/backup_${TIMESTAMP}"

TOTAL_SIZE=$(du -sh "${BACKUP_DIR}/${BACKUP_NAME}.tar.gz" | cut -f1)

# Step 4: Rotate old backups
echo ""
echo "[4/4] Rotating old backups (keeping last ${KEEP_DAYS} days)..."
DELETED=$(find "${BACKUP_DIR}" -name "paperless-backup-*.tar.gz" -mtime +${KEEP_DAYS} -print -delete | wc -l)
find "${BACKUP_DIR}" -name "paperless-backup-*.tar.gz.sha256" -mtime +${KEEP_DAYS} -delete 2>/dev/null || true
echo "      Deleted ${DELETED} old backup(s)"

# Summary
echo ""
echo "=============================================="
echo "  Backup Complete!"
echo "=============================================="
echo "  File: ${BACKUP_DIR}/${BACKUP_NAME}.tar.gz"
echo "  Size: ${TOTAL_SIZE}"
echo "  SHA256: $(cat "${BACKUP_DIR}/${BACKUP_NAME}.tar.gz.sha256" | cut -d' ' -f1)"
echo "=============================================="
