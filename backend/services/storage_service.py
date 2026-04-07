"""MinIO storage service for file upload/download.

Per MASTER_BUILD_PLAN Phase 10:
- MinIO S3-compatible self-hosted file storage
- Tenant-scoped file paths: {tenant_id}/{file_type}/{filename}
"""

import io
import uuid
from typing import BinaryIO

import structlog

from backend.core.config import settings

logger = structlog.get_logger()


class StorageService:
    """MinIO S3-compatible storage operations."""

    def __init__(self) -> None:
        self._client = None

    def _get_client(self):
        """Lazy-init MinIO client."""
        if self._client is None:
            from minio import Minio
            self._client = Minio(
                settings.MINIO_ENDPOINT,
                access_key=settings.MINIO_ACCESS_KEY,
                secret_key=settings.MINIO_SECRET_KEY,
                secure=settings.MINIO_SECURE,
            )
            # Ensure bucket exists
            if not self._client.bucket_exists(settings.MINIO_BUCKET):
                self._client.make_bucket(settings.MINIO_BUCKET)
                logger.info("minio_bucket_created", bucket=settings.MINIO_BUCKET)
        return self._client

    def _tenant_key(self, tenant_id: str, file_type: str, filename: str) -> str:
        """Generate tenant-scoped MinIO object key."""
        ext = filename.rsplit(".", 1)[-1] if "." in filename else "bin"
        unique = uuid.uuid4().hex[:12]
        return f"{tenant_id}/{file_type}/{unique}.{ext}"

    async def upload_file(
        self,
        tenant_id: str,
        file_data: BinaryIO | bytes,
        filename: str,
        content_type: str,
        file_type: str = "uploads",
    ) -> dict:
        """Upload a file to MinIO. Returns bucket, key, and size."""
        client = self._get_client()
        key = self._tenant_key(tenant_id, file_type, filename)

        if isinstance(file_data, bytes):
            data = io.BytesIO(file_data)
            size = len(file_data)
        else:
            data = file_data
            data.seek(0, 2)
            size = data.tell()
            data.seek(0)

        try:
            client.put_object(
                settings.MINIO_BUCKET,
                key,
                data,
                length=size,
                content_type=content_type,
            )
        except Exception as e:
            logger.error("minio_upload_failed", key=key, error=str(e), tenant_id=tenant_id)
            raise

        logger.info("file_uploaded", key=key, size=size, tenant_id=tenant_id)

        return {
            "bucket": settings.MINIO_BUCKET,
            "key": key,
            "size": size,
            "content_type": content_type,
        }

    async def download_file(self, key: str) -> bytes:
        """Download a file from MinIO."""
        client = self._get_client()
        try:
            response = client.get_object(settings.MINIO_BUCKET, key)
        except Exception as e:
            logger.error("minio_download_failed", key=key, error=str(e))
            raise
        try:
            return response.read()
        finally:
            response.close()
            response.release_conn()

    async def get_presigned_url(self, key: str, expires_hours: int = 1) -> str:
        """Generate a presigned download URL."""
        from datetime import timedelta
        client = self._get_client()
        return client.presigned_get_object(
            settings.MINIO_BUCKET,
            key,
            expires=timedelta(hours=expires_hours),
        )

    async def delete_file(self, key: str) -> None:
        """Delete a file from MinIO."""
        client = self._get_client()
        try:
            client.remove_object(settings.MINIO_BUCKET, key)
            logger.info("file_deleted", key=key)
        except Exception as e:
            logger.error("minio_delete_failed", key=key, error=str(e))
            raise


# Singleton
storage_service = StorageService()
