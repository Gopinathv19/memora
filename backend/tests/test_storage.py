import io

import pytest

from app.core.config import Settings
from app.core.errors import NotFoundError
from app.storage import LocalStorageBackend, S3StorageBackend
from app.storage import local as local_storage


class FakeS3:
    """The four boto3 calls S3StorageBackend makes, kept in a dict."""

    def __init__(self):
        self.objects: dict[tuple[str, str], bytes] = {}

    def upload_fileobj(self, fileobj, bucket, key, ExtraArgs=None):
        self.objects[(bucket, key)] = fileobj.read()

    def head_object(self, Bucket, Key):
        return {"ContentLength": len(self.objects[(Bucket, Key)])}

    def get_object(self, Bucket, Key):
        from botocore.exceptions import ClientError

        if (Bucket, Key) not in self.objects:
            raise ClientError({"Error": {"Code": "NoSuchKey"}}, "GetObject")
        return {"Body": io.BytesIO(self.objects[(Bucket, Key)])}

    def delete_object(self, Bucket, Key):
        self.objects.pop((Bucket, Key), None)


def _r2_settings(**overrides) -> Settings:
    values = dict(
        database_url="postgresql+psycopg://x/y",
        environment="production",
        r2_endpoint_url="https://acct.r2.cloudflarestorage.com",
        r2_access_key_id="id",
        r2_secret_access_key="secret",
        r2_bucket="memora-documents",
    )
    return Settings(**(values | overrides))


def _backend() -> tuple[S3StorageBackend, FakeS3]:
    backend = S3StorageBackend(_r2_settings())
    fake = FakeS3()
    backend._client = fake
    return backend, fake


def test_s3_round_trip():
    backend, fake = _backend()
    stored = backend.put("t/s/abc-file.pdf", io.BytesIO(b"%PDF-1.7"), "application/pdf")
    assert stored.storage_uri == "s3://memora-documents/t/s/abc-file.pdf"
    assert stored.size_bytes == 8
    with backend.open(stored.storage_uri) as stream:
        assert stream.read() == b"%PDF-1.7"
    backend.delete(stored.storage_uri)
    assert fake.objects == {}


def test_s3_owns_only_its_own_bucket():
    backend, _ = _backend()
    assert backend.owns("s3://memora-documents/t/s/file.pdf")
    assert not backend.owns("s3://other-bucket/t/s/file.pdf")
    assert not backend.owns("s3://memora-documents-2/file.pdf")
    assert not backend.owns("s3://memora-documents/")
    assert not backend.owns("file://t/s/file.pdf")


def test_s3_never_deletes_from_another_bucket():
    backend, fake = _backend()
    fake.objects[("other-bucket", "keep.pdf")] = b"x"
    backend.delete("s3://other-bucket/keep.pdf")
    assert fake.objects


def test_s3_missing_object_is_not_found():
    backend, _ = _backend()
    with pytest.raises(NotFoundError):
        backend.open("s3://memora-documents/nope.pdf")


def test_s3_ignores_uris_it_does_not_own():
    backend, fake = _backend()
    fake.objects[("memora-documents", "keep.pdf")] = b"x"
    backend.delete("file://keep.pdf")
    assert fake.objects


def test_s3_requires_credentials():
    with pytest.raises(RuntimeError, match="R2_SECRET_ACCESS_KEY"):
        S3StorageBackend(_r2_settings(r2_secret_access_key=""))


@pytest.mark.parametrize(
    ("environment", "expected"),
    [("local", LocalStorageBackend), ("production", S3StorageBackend)],
)
def test_environment_picks_the_backend(monkeypatch, tmp_path, environment, expected):
    settings = _r2_settings(environment=environment, storage_dir=str(tmp_path))
    monkeypatch.setattr(local_storage, "get_settings", lambda: settings)
    local_storage.get_storage.cache_clear()
    try:
        assert isinstance(local_storage.get_storage(), expected)
    finally:
        local_storage.get_storage.cache_clear()
