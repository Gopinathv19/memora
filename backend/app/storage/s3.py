from typing import BinaryIO

from app.core.config import Settings
from app.core.errors import NotFoundError, ValidationError
from app.storage.base import StorageBackend, StoredObject


class S3StorageBackend(StorageBackend):
    """Writes objects to an S3-compatible bucket (Cloudflare R2 in production).

    URIs look like `s3://<bucket>/<key>`. The bucket is part of the URI so a
    row always says exactly where its bytes live, even if the configured
    bucket changes later.
    """

    scheme = "s3://"

    def __init__(self, settings: Settings):
        missing = [
            env
            for env, value in (
                ("R2_ENDPOINT_URL", settings.r2_endpoint_url),
                ("R2_ACCESS_KEY_ID", settings.r2_access_key_id),
                ("R2_SECRET_ACCESS_KEY", settings.r2_secret_access_key),
                ("R2_BUCKET", settings.r2_bucket),
            )
            if not value
        ]
        if missing:
            raise RuntimeError(
                f"{', '.join(missing)} not set; production storage needs them in backend/.env"
            )
        import boto3
        from botocore.config import Config

        self.bucket = settings.r2_bucket
        self._client = boto3.client(
            "s3",
            endpoint_url=settings.r2_endpoint_url,
            aws_access_key_id=settings.r2_access_key_id,
            aws_secret_access_key=settings.r2_secret_access_key,
            # R2 ignores the region but boto3 insists on one; "auto" is R2's own.
            region_name="auto",
            config=Config(retries={"max_attempts": 3, "mode": "standard"}),
        )

    def owns(self, storage_uri: str) -> bool:
        # Only our own bucket: an s3:// URI registered as metadata may point
        # at someone else's, which these credentials must never touch.
        prefix = f"{self.scheme}{self.bucket}/"
        return storage_uri.startswith(prefix) and len(storage_uri) > len(prefix)

    def _split(self, storage_uri: str) -> tuple[str, str]:
        if not self.owns(storage_uri):
            raise ValidationError(
                f"storage_uri {storage_uri!r} is not in bucket {self.bucket!r}"
            )
        return self.bucket, storage_uri[len(self.scheme) + len(self.bucket) + 1:]

    def put(
        self, key: str, fileobj: BinaryIO, content_type: str | None = None
    ) -> StoredObject:
        extra = {"ContentType": content_type} if content_type else {}
        # upload_fileobj streams in parts, so a large upload is never held
        # in memory whole.
        self._client.upload_fileobj(fileobj, self.bucket, key, ExtraArgs=extra)
        head = self._client.head_object(Bucket=self.bucket, Key=key)
        return StoredObject(
            storage_uri=f"{self.scheme}{self.bucket}/{key}",
            size_bytes=int(head["ContentLength"]),
        )

    def open(self, storage_uri: str) -> BinaryIO:
        from botocore.exceptions import ClientError

        bucket, key = self._split(storage_uri)
        try:
            response = self._client.get_object(Bucket=bucket, Key=key)
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in ("NoSuchKey", "404"):
                raise NotFoundError("The stored object for this source is missing") from exc
            raise
        # botocore's StreamingBody reads, iterates in chunks and works as a
        # context manager, which is everything callers do with a BinaryIO.
        return response["Body"]

    def delete(self, storage_uri: str) -> None:
        try:
            bucket, key = self._split(storage_uri)
        except ValidationError:
            # A file:// URI, another bucket or an https:// link is not ours to
            # delete; dropping the database row is the whole operation.
            return
        # S3 delete is idempotent: a missing key is not an error.
        self._client.delete_object(Bucket=bucket, Key=key)
