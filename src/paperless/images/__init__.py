"""
Secure image uploads.

    images/
      storage/      StorageProvider, LocalStorageProvider, S3StorageProvider
      processing/   magic-byte sniffing, validation, re-encoding, variants
      scanning.py   optional ClamAV hook (PAPERLESS_IMAGES_CLAMAV_ENABLED)
      service.py    upload / delete use cases (authorization + audit)
      tasks.py      Celery jobs: variant generation, file cleanup, sweeps
      serializers.py, views.py, throttles.py   REST API (/api/images/)
"""
