#!/bin/bash
# =============================================================================
# Paperless-ngx Backup Verification Script
# =============================================================================
# Usage: ./verify-backup.sh <backup-file.tar.gz>
#
# Verifies a backup file's integrity without restoring it:
#   - Checks archive integrity
#   - Verifies checksum (if .sha256 file exists)
#   - Lists archive contents
#   - Validates database dump is present
#   - Validates document export is present
#   - Optionally runs sanity check on current installation
# =============================================================================

set -euo pipefail

COMPOSE_FILE="docker-compose.dev.yml"
PASSED=0
FAILED=0
WARNINGS=0

pass() { echo "  [PASS] $1"; ((PASSED++)); }
fail() { echo "  [FAIL] $1"; ((FAILED++)); }
warn() { echo "  [WARN] $1"; ((WARNINGS++)); }

# Validate arguments
if [ $# -lt 1 ]; then
    echo "Usage: $0 <backup-file.tar.gz>"
    exit 1
fi

BACKUP_FILE="$1"

echo "=============================================="
echo "  Backup Verification"
echo "  File: ${BACKUP_FILE}"
echo "=============================================="
echo ""

# Check file exists
if [ ! -f "${BACKUP_FILE}" ]; then
    fail "Backup file not found: ${BACKUP_FILE}"
    echo ""
    echo "Result: ${FAILED} FAILED"
    exit 1
fi
pass "Backup file exists"

# Check file size
FILE_SIZE=$(du -sh "${BACKUP_FILE}" | cut -f1)
echo "       Size: ${FILE_SIZE}"

# Verify checksum
CHECKSUM_FILE="${BACKUP_FILE}.sha256"
if [ -f "${CHECKSUM_FILE}" ]; then
    if sha256sum -c "${CHECKSUM_FILE}" > /dev/null 2>&1; then
        pass "SHA256 checksum matches"
    else
        fail "SHA256 checksum DOES NOT match - file may be corrupted"
    fi
else
    warn "No checksum file found (${CHECKSUM_FILE})"
fi

# Test archive integrity
if tar -tzf "${BACKUP_FILE}" > /dev/null 2>&1; then
    pass "Archive integrity OK (gzip + tar valid)"
else
    fail "Archive is corrupted (cannot read tar.gz)"
    echo ""
    echo "Result: ${PASSED} passed, ${FAILED} failed, ${WARNINGS} warnings"
    exit 1
fi

# Extract to temp dir for inspection
VERIFY_DIR=$(mktemp -d)
trap "rm -rf ${VERIFY_DIR}" EXIT
tar -xzf "${BACKUP_FILE}" -C "${VERIFY_DIR}"

# Check for database dump
DB_DUMP=$(find "${VERIFY_DIR}" -name "*_db.dump" -type f | head -1)
if [ -n "${DB_DUMP}" ]; then
    DB_SIZE=$(du -sh "${DB_DUMP}" | cut -f1)
    pass "Database dump found (${DB_SIZE})"
else
    fail "No database dump found in archive"
fi

# Check for document export
DOCS_ZIP=$(find "${VERIFY_DIR}" -name "*_docs.zip" -type f | head -1)
if [ -n "${DOCS_ZIP}" ]; then
    DOCS_SIZE=$(du -sh "${DOCS_ZIP}" | cut -f1)
    pass "Document export found (${DOCS_SIZE})"

    # Verify ZIP integrity
    if unzip -t "${DOCS_ZIP}" > /dev/null 2>&1; then
        pass "Document export ZIP integrity OK"
    else
        fail "Document export ZIP is corrupted"
    fi
else
    warn "No document export found (may be empty database)"
fi

# List archive contents
echo ""
echo "  Archive contents:"
tar -tzf "${BACKUP_FILE}" | while read -r line; do
    echo "    ${line}"
done

# Optionally run sanity check on live system
echo ""
echo "  Running sanity check on current installation..."
if docker compose -f "${COMPOSE_FILE}" exec -T webserver \
    python /usr/src/paperless/src/manage.py document_sanity_checker --no-progress-bar 2>/dev/null; then
    pass "Live system sanity check passed"
else
    warn "Could not run sanity check (is the system running?)"
fi

# Summary
echo ""
echo "=============================================="
echo "  Verification Summary"
echo "=============================================="
echo "  Passed:   ${PASSED}"
echo "  Failed:   ${FAILED}"
echo "  Warnings: ${WARNINGS}"
echo "=============================================="

if [ ${FAILED} -gt 0 ]; then
    echo "  Status: FAILED - DO NOT USE THIS BACKUP"
    exit 1
else
    echo "  Status: OK"
    exit 0
fi
