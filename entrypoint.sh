#!/bin/sh

echo "⏳ Waiting for Postgres..."

# Allow overriding DB/Redis hosts via environment (Render will set these)
DB_HOST=${DB_HOST:-db}
DB_PORT=${DB_PORT:-5432}
REDIS_HOST=${REDIS_HOST:-redis}
REDIS_PORT=${REDIS_PORT:-6379}

if [ -n "$DATABASE_URL" ]; then
  DB_HOST=$(python - <<'PY'
import os
from urllib.parse import urlparse
url = os.getenv('DATABASE_URL', '')
if url:
    u = urlparse(url)
    if u.hostname:
        print(u.hostname)
PY
)
  DB_PORT=$(python - <<'PY'
import os
from urllib.parse import urlparse
url = os.getenv('DATABASE_URL', '')
if url:
    u = urlparse(url)
    if u.port:
        print(u.port)
PY
)
fi

if [ -n "$REDIS_URL" ]; then
  REDIS_HOST=$(python - <<'PY'
import os
from urllib.parse import urlparse
url = os.getenv('REDIS_URL', '')
if url:
    u = urlparse(url)
    if u.hostname:
        print(u.hostname)
PY
)
  REDIS_PORT=$(python - <<'PY'
import os
from urllib.parse import urlparse
url = os.getenv('REDIS_URL', '')
if url:
    u = urlparse(url)
    if u.port:
        print(u.port)
PY
)
fi

DB_PORT=${DB_PORT:-5432}
REDIS_PORT=${REDIS_PORT:-6379}

while ! nc -z "$DB_HOST" "$DB_PORT"; do
  sleep 1
done

echo "✅ Postgres is ready!"

echo "⏳ Waiting for Redis..."

while ! nc -z "$REDIS_HOST" "$REDIS_PORT"; do
  sleep 1
done

echo "✅ Redis is ready!"

echo "📦 Applying migrations..."
python manage.py migrate

echo "📁 Collecting static files..."
python manage.py collectstatic --noinput

# Optional superuser
if [ "$DJANGO_SUPERUSER_USERNAME" ]; then
  echo "👤 Creating superuser..."
  python manage.py createsuperuser \
    --noinput \
    --username $DJANGO_SUPERUSER_USERNAME \
    --email $DJANGO_SUPERUSER_EMAIL || true
fi

# Mode switch
if [ "$ENV" = "prod" ]; then
  echo "🚀 Starting Gunicorn..."
  exec gunicorn config.wsgi:application --bind 0.0.0.0:8000
else
  echo "🛠 Starting Dev Server..."
  exec python manage.py runserver 0.0.0.0:8000
fi