"""
One small storage interface for every uploaded/generated file main.py and
jobs.py used to read/write straight off disk: the original uploaded
document or photos, OCR-rendered/rotated page images. STORAGE_BACKEND
picks the implementation -- "local" (default, under UPLOAD_DIR, today's
behaviour) or "s3" (any S3-compatible bucket, e.g. Supabase Storage) --
every caller uses the same five calls either way.

Keys look like "users/<user_id>/books/<book_id>/original.pdf",
"users/<user_id>/books/<book_id>/original_images/0001.jpg", or
"users/<user_id>/books/<book_id>/pages/0003.jpg". LocalStorage maps a key
to a real path by collapsing "users/<id>/books/<id>/" down to "<id>/<id>/"
-- the exact layout backend/uploads/ has always used -- so an existing
local dev upload folder keeps working with no data migration (an already-
saved DocumentPage.image_path, a full absolute path from before this
module existed, is also accepted as-is; see LocalStorage._resolve).

The bucket is private -- nothing here ever generates a public/presigned
URL. A file reaches the browser only by the backend streaming it through
after the usual owner check (see main.py's get_book_file/get_page_image),
same as today.
"""
import glob
import logging
import os
import re
from pathlib import Path
from urllib.parse import urlparse

from env_utils import get_env

logger = logging.getLogger(__name__)

_USERS_BOOKS_PREFIX = re.compile(r"^users/(\d+)/books/(\d+)/")


class StorageNotConfiguredError(Exception):
    """STORAGE_BACKEND=s3 was chosen but a required S3_* setting is missing."""


def _as_bytes(data) -> bytes:
    if isinstance(data, bytes):
        return data
    if isinstance(data, bytearray):
        return bytes(data)
    if hasattr(data, "read"):
        return data.read()
    raise TypeError(f"Expected bytes or a file-like object, got {type(data).__name__}")


class LocalStorage:
    """Today's behaviour: files under UPLOAD_DIR, one folder per user/book."""

    def __init__(self, root: Path):
        self.root = Path(root)

    def _resolve(self, key: str) -> Path:
        # An already-saved DocumentPage.image_path from before this module
        # existed is a full absolute filesystem path, not a key -- use it
        # as-is rather than trying to reinterpret it.
        if os.path.isabs(key):
            return Path(key)
        relative = _USERS_BOOKS_PREFIX.sub(r"\1/\2/", key)
        return (self.root / relative).resolve()

    def _key_from_path(self, path) -> str:
        relative = Path(path).resolve().relative_to(self.root.resolve()).as_posix()
        parts = relative.split("/", 2)
        if len(parts) >= 2 and parts[0].isdigit() and parts[1].isdigit():
            rest = "/" + parts[2] if len(parts) > 2 else ""
            return f"users/{parts[0]}/books/{parts[1]}{rest}"
        return relative

    def save(self, key: str, data, content_type: str | None = None) -> None:
        path = self._resolve(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        if hasattr(data, "read"):
            import shutil
            with open(path, "wb") as out:
                shutil.copyfileobj(data, out)
        else:
            path.write_bytes(_as_bytes(data))

    def read(self, key: str) -> bytes:
        path = self._resolve(key)
        try:
            return path.read_bytes()
        except FileNotFoundError:
            raise FileNotFoundError(key)

    def open(self, key: str):
        path = self._resolve(key)
        try:
            return open(path, "rb")
        except FileNotFoundError:
            raise FileNotFoundError(key)

    def local_path(self, key: str) -> Path | None:
        """A real filesystem path for `key` -- lets callers use FileResponse (Range/caching) for the local backend. None for a backend with no such thing (S3)."""
        path = self._resolve(key)
        return path if path.is_file() else None

    def exists(self, key: str) -> bool:
        return self._resolve(key).is_file()

    def delete(self, key: str) -> None:
        path = self._resolve(key)
        try:
            path.unlink()
        except FileNotFoundError:
            pass

    def delete_prefix(self, prefix: str) -> None:
        import shutil
        path = self._resolve(prefix)
        if path.is_dir():
            shutil.rmtree(path, ignore_errors=True)
        else:
            # The prefix is a filename fragment (e.g. ".../original."),
            # not a real directory -- remove each file it actually matches.
            for match in glob.glob(str(path) + "*"):
                try:
                    os.remove(match)
                except OSError:
                    pass

    def list_prefix(self, prefix: str) -> list:
        path = self._resolve(prefix)
        if path.is_dir():
            matches = sorted(p for p in path.iterdir() if p.is_file())
        else:
            matches = sorted(Path(p) for p in glob.glob(str(path) + "*"))
        return [self._key_from_path(m) for m in matches]


class S3Storage:
    """Any S3-compatible bucket (Supabase Storage, etc.) -- private, path-style addressing."""

    def __init__(self, endpoint_url: str, region: str, bucket: str, access_key_id: str, secret_access_key: str):
        import boto3
        from botocore.config import Config as BotoConfig

        self.bucket = bucket
        self.client = boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            region_name=region,
            aws_access_key_id=access_key_id,
            aws_secret_access_key=secret_access_key,
            config=BotoConfig(s3={"addressing_style": "path"}),
        )

    def save(self, key: str, data, content_type: str | None = None) -> None:
        kwargs = {"ContentType": content_type} if content_type else {}
        self.client.put_object(Bucket=self.bucket, Key=key, Body=_as_bytes(data), **kwargs)

    def read(self, key: str) -> bytes:
        return self.open(key).read()

    def open(self, key: str):
        from botocore.exceptions import ClientError
        try:
            return self.client.get_object(Bucket=self.bucket, Key=key)["Body"]
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in ("NoSuchKey", "404"):
                raise FileNotFoundError(key)
            raise

    def local_path(self, key: str) -> Path | None:
        return None

    def exists(self, key: str) -> bool:
        from botocore.exceptions import ClientError
        try:
            self.client.head_object(Bucket=self.bucket, Key=key)
            return True
        except ClientError:
            return False

    def delete(self, key: str) -> None:
        self.client.delete_object(Bucket=self.bucket, Key=key)

    def delete_prefix(self, prefix: str) -> None:
        # One DeleteObject call per key, not the batch DeleteObjects API --
        # confirmed against a real Supabase Storage bucket (Fix Phase 7B
        # item 5's real check) that DeleteObjects isn't supported there
        # ("ClientError... DeleteObjects operation"), while plain
        # DeleteObject works on every S3-compatible service tried so far.
        # Slower for a book with many page images, but correct everywhere.
        paginator = self.client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
            for obj in page.get("Contents", []):
                self.client.delete_object(Bucket=self.bucket, Key=obj["Key"])

    def list_prefix(self, prefix: str) -> list:
        paginator = self.client.get_paginator("list_objects_v2")
        keys = []
        for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
            keys.extend(obj["Key"] for obj in page.get("Contents", []))
        return sorted(keys)


def default_local_root() -> Path:
    """
    The local upload root LocalStorage uses by default, and the one a
    pre-Phase-7B DocumentPage.image_path (a full absolute path, not a key)
    was always saved under -- exposed so main.py's legacy-path containment
    check (see _resolve_page_image_key) doesn't have to duplicate this.
    """
    return Path(get_env("UPLOAD_DIR") or (Path(__file__).resolve().parent / "uploads"))


def _is_valid_https_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme == "https" and bool(parsed.netloc)


def get_storage():
    backend = (get_env("STORAGE_BACKEND", "local") or "local").lower()

    if backend == "local":
        return LocalStorage(default_local_root())

    if backend == "s3":
        settings = {
            "endpoint_url": get_env("S3_ENDPOINT_URL"),
            "region": get_env("S3_REGION"),
            "bucket": get_env("S3_BUCKET"),
            "access_key_id": get_env("S3_ACCESS_KEY_ID"),
            "secret_access_key": get_env("S3_SECRET_ACCESS_KEY"),
        }
        missing = [f"S3_{name.upper()}" for name, value in settings.items() if not value]
        if missing:
            raise StorageNotConfiguredError(
                f"STORAGE_BACKEND=s3 but missing required setting(s): {', '.join(missing)}. "
                "Set them in backend/.env -- see .env.example."
            )
        # Never include the value itself -- a malformed endpoint here is
        # almost always a hidden space/newline (get_env above already
        # strips those) or a copy-paste that dropped the "https://" --
        # either way, printing the broken value back wouldn't help and
        # this is exactly the kind of setting worth keeping out of logs.
        if not _is_valid_https_url(settings["endpoint_url"]):
            raise StorageNotConfiguredError(
                "S3_ENDPOINT_URL looks malformed -- expected a full https:// URL, "
                "e.g. https://<project-ref>.supabase.co/storage/v1/s3"
            )
        return S3Storage(**settings)

    raise StorageNotConfiguredError(f"Unknown STORAGE_BACKEND '{backend}'. Use 'local' or 's3'.")


storage = get_storage()
