#!/bin/sh

echo "Waiting for DB..."

DB_HOST=${DB_HOST:-db}
DB_PORT=${DB_PORT:-5432}

if [ -n "$DATABASE_URL" ]; then
  DB_HOST=${DB_HOST:-$(python - <<'PY'
import os
from urllib.parse import urlparse
url = os.getenv('DATABASE_URL', '')
if url:
    u = urlparse(url)
    if u.hostname:
        print(u.hostname)
PY
)}
  DB_PORT=${DB_PORT:-$(python - <<'PY'
import os
from urllib.parse import urlparse
url = os.getenv('DATABASE_URL', '')
if url:
    u = urlparse(url)
    if u.port:
        print(u.port)
PY
)}
fi

DB_PORT=${DB_PORT:-5432}

while ! nc -z "$DB_HOST" "$DB_PORT"; do
  sleep 1
done

echo "Waiting for migrations..."

sleep 5

exec "$@"