from functools import lru_cache

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

from paperless.images.storage.base import InvalidStorageKey
from paperless.images.storage.base import StorageProvider
from paperless.images.storage.base import validate_key


@lru_cache(maxsize=1)
def get_storage() -> StorageProvider:
    backend = settings.IMAGES_STORAGE_BACKEND
    if backend == "local":
        from paperless.images.storage.local import LocalStorageProvider

        return LocalStorageProvider(settings.IMAGES_STORAGE_DIR)
    if backend == "s3":
        from paperless.images.storage.s3 import S3StorageProvider

        return S3StorageProvider(
            bucket=settings.IMAGES_S3_BUCKET,
            prefix=settings.IMAGES_S3_PREFIX,
            endpoint_url=settings.IMAGES_S3_ENDPOINT_URL,
            region_name=settings.IMAGES_S3_REGION,
        )
    raise ImproperlyConfigured(f"Unknown PAPERLESS_IMAGES_STORAGE: {backend!r}")


__all__ = ["InvalidStorageKey", "StorageProvider", "get_storage", "validate_key"]
