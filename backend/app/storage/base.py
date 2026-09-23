from dataclasses import dataclass
from typing import BinaryIO, Protocol


@dataclass(frozen=True)
class StoredObject:
    """The result of persisting bytes: where they went and how many there were."""

    storage_uri: str
    size_bytes: int


class StorageBackend(Protocol):
    """Where source bytes live.

    Only two operations are needed, and both are addressed by an opaque
    `storage_uri` that the backend itself mints. Callers never build a path.
    That is what makes swapping the local implementation for S3 later a
    one-line change in `get_storage()` rather than a change at every call site.
    """

    def put(self, key: str, fileobj: BinaryIO, content_type: str | None) -> StoredObject:
        """Stream `fileobj` to storage under `key` and return its location."""
        ...

    def open(self, storage_uri: str) -> BinaryIO:
        """Open a previously stored object for reading."""
        ...

    def delete(self, storage_uri: str) -> None:
        """Remove a stored object. Missing objects are not an error."""
        ...
