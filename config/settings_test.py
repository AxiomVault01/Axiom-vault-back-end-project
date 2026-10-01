"""Settings for the test suite: runs without Redis or real email.

Tests use the same Postgres as the app when a local server is reachable (Django
creates and drops a separate `test_<name>` database), and fall back to an
in-memory SQLite database otherwise so the suite still runs without Postgres.
"""

import socket

from .settings import *  # noqa: F401,F403
from .settings import DATABASES

# Only local servers: the test run creates and drops a database, which must
# never happen on a shared or production server such as Render.
LOCAL_DB_HOSTS = {"localhost", "127.0.0.1", "db"}


def _local_postgres_reachable(db):
    host = db.get("HOST") or ""
    if "postgresql" not in db.get("ENGINE", "") or host not in LOCAL_DB_HOSTS:
        return False
    try:
        with socket.create_connection((host, int(db.get("PORT") or 5432)), timeout=1):
            return True
    except OSError:
        return False


if not _local_postgres_reachable(DATABASES["default"]):
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": ":memory:",
        }
    }

CACHES = {
    "default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}
}

# Sent emails are captured in django.core.mail.outbox instead of going to Gmail.
EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"

# .delay() runs the task immediately in-process, so no Redis broker is needed.
CELERY_TASK_ALWAYS_EAGER = True
CELERY_TASK_EAGER_PROPAGATES = True

PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]

# Fixed, test-only key long enough for JWT signing; never used outside the test suite.
SECRET_KEY = "test-only-secret-key-for-pytest-0123456789abcdef"
