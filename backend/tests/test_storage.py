"""
Direct tests of storage.py's own interface (save/read/open/exists/delete/
delete_prefix/list_prefix) against both backends -- LocalStorage against a
throwaway tmp_path, S3Storage against a moto-mocked bucket. No HTTP
request, FastAPI app, or database involved at all; see test_api.py's own
"PHASE 7B" section for the same upload/retry/rotate/delete flow exercised
end-to-end through the real API against both backends.
"""
import os
import uuid
from pathlib import Path

import boto3
import moto
import pytest
from dotenv import dotenv_values

import storage

_TEST_S3_SETTING_NAMES = [
    "TEST_S3_ENDPOINT_URL", "TEST_S3_REGION", "TEST_S3_BUCKET",
    "TEST_S3_ACCESS_KEY_ID", "TEST_S3_SECRET_ACCESS_KEY",
]


def real_s3_settings():
    """
    Reads TEST_S3_* the same way test_api.py reads TEST_DATABASE_URL -- a
    real os.environ var wins (e.g. set by CI), otherwise read backend/.env
    directly with dotenv_values() (never load_dotenv(), which would also
    pull in every other backend/.env value this suite deliberately runs
    without). Returns None if any of the five settings is missing -- the
    real round-trip below is skipped in that case, never attempted with a
    partial/placeholder config.
    """
    env_file = dotenv_values(Path(__file__).resolve().parent.parent / ".env")
    values = {name: os.getenv(name) or env_file.get(name) for name in _TEST_S3_SETTING_NAMES}
    if not all(values.values()):
        return None
    return values


@pytest.fixture
def local(tmp_path):
    return storage.LocalStorage(tmp_path)


@pytest.fixture
def s3():
    with moto.mock_aws():
        boto3.client("s3", region_name="us-east-1").create_bucket(Bucket="test-bucket")
        yield storage.S3Storage(
            endpoint_url=None, region="us-east-1", bucket="test-bucket",
            access_key_id="testing", secret_access_key="testing",
        )


# Runs every test below against both backends in turn via the `backend`
# fixture (indirect-parametrized onto the `local`/`s3` fixtures above) --
# anything that passes for one and not the other is a backend-specific bug.
@pytest.fixture(params=["local", "s3"])
def backend(request):
    return request.getfixturevalue(request.param)


def test_save_bytes_then_read_round_trips(backend):
    backend.save("users/1/books/2/original.txt", b"hello world", content_type="text/plain")
    assert backend.read("users/1/books/2/original.txt") == b"hello world"


def test_save_file_like_object_then_read_round_trips(backend):
    import io
    backend.save("users/1/books/2/original.txt", io.BytesIO(b"from a file-like object"))
    assert backend.read("users/1/books/2/original.txt") == b"from a file-like object"


def test_exists_true_after_save_false_before(backend):
    key = "users/1/books/2/original.txt"
    assert backend.exists(key) is False
    backend.save(key, b"data")
    assert backend.exists(key) is True


def test_read_missing_key_raises_file_not_found(backend):
    with pytest.raises(FileNotFoundError):
        backend.read("users/1/books/2/never-saved.txt")


def test_open_returns_a_readable_stream(backend):
    key = "users/1/books/2/original.txt"
    backend.save(key, b"streamed content")
    stream = backend.open(key)
    try:
        assert stream.read() == b"streamed content"
    finally:
        stream.close()


def test_delete_removes_the_key(backend):
    key = "users/1/books/2/original.txt"
    backend.save(key, b"data")
    backend.delete(key)
    assert backend.exists(key) is False


def test_delete_missing_key_does_not_raise(backend):
    backend.delete("users/1/books/2/never-saved.txt")  # must not raise


def test_list_prefix_finds_only_matching_keys(backend):
    backend.save("users/1/books/2/original.pdf", b"a")
    backend.save("users/1/books/2/pages/0001.jpg", b"b")
    backend.save("users/1/books/3/original.pdf", b"c")  # a different book

    doc_keys = backend.list_prefix("users/1/books/2/original.")
    assert doc_keys == ["users/1/books/2/original.pdf"]

    page_keys = backend.list_prefix("users/1/books/2/pages/")
    assert page_keys == ["users/1/books/2/pages/0001.jpg"]

    other_book_keys = backend.list_prefix("users/1/books/3/")
    assert other_book_keys == ["users/1/books/3/original.pdf"]


def test_list_prefix_empty_when_nothing_matches(backend):
    assert backend.list_prefix("users/1/books/2/") == []


def test_delete_prefix_removes_every_matching_key_and_nothing_else(backend):
    backend.save("users/1/books/2/original.pdf", b"a")
    backend.save("users/1/books/2/pages/0001.jpg", b"b")
    backend.save("users/1/books/2/pages/0002.jpg", b"c")
    backend.save("users/1/books/3/original.pdf", b"keep me")  # a different book

    backend.delete_prefix("users/1/books/2/")

    assert backend.list_prefix("users/1/books/2/") == []
    assert backend.exists("users/1/books/3/original.pdf") is True


def test_local_path_is_a_real_path_for_local_none_for_s3():
    import boto3 as _boto3
    import moto as _moto

    local_backend = storage.LocalStorage(storage.default_local_root())
    with _moto.mock_aws():
        _boto3.client("s3", region_name="us-east-1").create_bucket(Bucket="lp-test-bucket")
        s3_backend = storage.S3Storage(
            endpoint_url=None, region="us-east-1", bucket="lp-test-bucket",
            access_key_id="testing", secret_access_key="testing",
        )
        assert s3_backend.local_path("users/1/books/2/original.pdf") is None

    # A key that was never saved has no real path to return either, local
    # or not -- local_path only ever points at something that actually exists.
    assert local_backend.local_path("users/999999/books/999999/never-saved.pdf") is None


def test_local_storage_accepts_a_pre_phase_7b_absolute_path(tmp_path):
    """
    Backward compatibility (Phase 7B item 6): a DocumentPage.image_path
    saved before storage.py existed is a full absolute filesystem path,
    not a "users/<id>/books/<id>/..." key -- LocalStorage must still read
    it directly rather than trying to reinterpret it as a key.
    """
    legacy_dir = tmp_path / "legacy_layout" / "5" / "12"
    legacy_dir.mkdir(parents=True)
    legacy_file = legacy_dir / "pages" / "0001.jpg"
    legacy_file.parent.mkdir(parents=True)
    legacy_file.write_bytes(b"old page image bytes")

    local_backend = storage.LocalStorage(tmp_path / "legacy_layout")
    absolute_key = str(legacy_file)

    assert local_backend.exists(absolute_key) is True
    assert local_backend.read(absolute_key) == b"old page image bytes"
    assert local_backend.local_path(absolute_key) == legacy_file


def test_local_storage_new_keys_map_onto_the_existing_uploads_layout(tmp_path):
    """
    Phase 7B item 6: a fresh key under the new "users/<id>/books/<id>/..."
    scheme must land at exactly the same on-disk path backend/uploads/ has
    always used (UPLOAD_DIR/<user_id>/<book_id>/...), so an existing local
    dev upload folder keeps working with no data migration.
    """
    local_backend = storage.LocalStorage(tmp_path)
    local_backend.save("users/5/books/12/original.pdf", b"doc bytes")
    assert (tmp_path / "5" / "12" / "original.pdf").read_bytes() == b"doc bytes"


def test_real_s3_save_exists_read_delete_prefix_round_trip():
    """
    Phase 7B item 5: if a real (ideally dedicated test-only) S3-compatible
    bucket is configured via TEST_S3_* in backend/.env, run a small real
    round trip against it instead of just the moto fake, then clean up
    everything this test created. Skipped (not failed) if not configured.
    """
    settings = real_s3_settings()
    if settings is None:
        pytest.skip(
            "TEST_S3_* not fully set in backend/.env -- skipping the real S3 round-trip check."
        )

    backend = storage.S3Storage(
        endpoint_url=settings["TEST_S3_ENDPOINT_URL"],
        region=settings["TEST_S3_REGION"],
        bucket=settings["TEST_S3_BUCKET"],
        access_key_id=settings["TEST_S3_ACCESS_KEY_ID"],
        secret_access_key=settings["TEST_S3_SECRET_ACCESS_KEY"],
    )

    # A random, test-run-unique prefix so this can never collide with
    # anything else in the bucket, real or from a concurrent test run.
    test_prefix = f"phase7b-real-s3-check/{uuid.uuid4().hex}/"
    key = test_prefix + "roundtrip.txt"
    try:
        assert backend.exists(key) is False
        backend.save(key, b"phase 7b real s3 round trip", content_type="text/plain")
        assert backend.exists(key) is True
        assert backend.read(key) == b"phase 7b real s3 round trip"
        stream = backend.open(key)
        try:
            assert stream.read() == b"phase 7b real s3 round trip"
        finally:
            stream.close()
        assert backend.list_prefix(test_prefix) == [key]
    finally:
        backend.delete_prefix(test_prefix)

    assert backend.exists(key) is False
    assert backend.list_prefix(test_prefix) == []
