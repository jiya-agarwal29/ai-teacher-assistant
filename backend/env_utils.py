"""
One helper for every environment-setting read in the backend.

Pasting a secret into Render's dashboard, a shell export, or a .env file
very easily picks up a stray leading/trailing space or a trailing newline
(copy-pasting from a password manager or a terminal is the usual culprit) --
that extra whitespace is invisible wherever the value gets echoed back, but
turns "postgresql://..." into something `int()`/a URL parser/boto3 rejects
with a confusing error (see README's/DEPLOY.md's troubleshooting notes).
Every module in this backend reads its settings through get_env() instead
of os.getenv() directly, so this is fixed in exactly one place.
"""
import os


def get_env(name: str, default: str | None = None) -> str | None:
    """
    Like os.getenv(), but strips whitespace (including a trailing newline)
    from the raw value first. A value that's empty -- or nothing but
    whitespace -- after stripping is treated the same as unset: there's no
    meaningful difference between "this setting was never set" and "someone
    pasted a blank line into it" for anything this project reads from the
    environment.
    """
    raw = os.environ.get(name)
    if raw is None:
        return default
    stripped = raw.strip()
    return stripped if stripped else default
