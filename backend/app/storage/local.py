import re
import shutil
import uuid
from functools import lru_cache
from pathlib import Path
from typing import BinaryIO

from app.core.config import get_settings
from app.core.errors import NotFoundError, ValidationError
from app.storage.base import StorageBackend, StoredObject

# Anything outside this set is replaced, so a hostile filename cannot escape
# the storage root or inject path segments.
_UNSAFE = re.compile(r"[^A-Za-z0-9._-]")

MAX_UPLOAD_BYTES = 50 * 1024 * 1024  # 50 MiB, generous for the MVP.


def safe_filename(name: str | None) -> str:
    """Reduce a client-supplied filename to something safe to write."""
    if not name:
        return "upload.bin"
    cleaned = _UNSAFE.sub("_", Path(name).name).strip("._-")
    return cleaned[:200] or "upload.bin"


def build_key(tenant_id: uuid.UUID, subject_id: uuid.UUID, filename: str | None) -> str:
    """Mint a collision-free storage key that mirrors the ownership chain.

    Laying objects out by tenant and subject means a tenant's bytes can be
    located -- or bulk-deleted -- without consulting the database.
    """
    return f"{tenant_id}/{subject_id}/{uuid.uuid4().hex}-{safe_filename(filename)}"


class LocalStorageBackend(StorageBackend):
    """Writes objects to a directory on local disk under a `file://` URI.

    Fine for the MVP and for local development. Swapping in an S3 backend later
    means implementing the same three methods and returning `s3://` URIs.
    """

    scheme = "file://"

    def __init__(self, root: Path):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _resolve(self, storage_uri: str) -> Path:
        if not storage_uri.startswith(self.scheme):
            raise ValidationError(
                f"storage_uri {storage_uri!r} is not managed by local storage"
            )
        key = storage_uri[len(self.scheme):]
        path = (self.root / key).resolve()
        # Defence in depth: even with a sanitized key, never read or write
        # outside the storage root.
        if not path.is_relative_to(self.root):
            raise ValidationError("storage_uri escapes the storage root")
        return path

    def put(
        self, key: str, fileobj: BinaryIO, content_type: str | None = None
    ) -> StoredObject:
        path = (self.root / key).resolve()
        if not path.is_relative_to(self.root):
            raise ValidationError("storage key escapes the storage root")
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("wb") as dest:
            shutil.copyfileobj(fileobj, dest, length=1024 * 1024)
        return StoredObject(
            storage_uri=f"{self.scheme}{key}", size_bytes=path.stat().st_size
        )

    def open(self, storage_uri: str) -> BinaryIO:
        path = self._resolve(storage_uri)
        if not path.exists():
            raise NotFoundError("The stored object for this source is missing")
        return path.open("rb")

    def delete(self, storage_uri: str) -> None:
        try:
            self._resolve(storage_uri).unlink(missing_ok=True)
        except ValidationError:
            # An externally-registered URI (s3://, https://) is not ours to
            # delete; dropping the database row is the whole operation.
            return


@lru_cache
def get_storage() -> StorageBackend:
    return LocalStorageBackend(Path(get_settings().storage_dir))
