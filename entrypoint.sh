#!/bin/sh
set -e

# ==========================================================
# 1. LIGHTWEIGHT HOST PARSING FOR RENDER / PRODUCTION
# ==========================================================
if [ -n "$DATABASE_URL" ]; then
  # Strips everything up to the '@' and everything after the ':' or '/'
  DB_HOST=$(echo "$DATABASE_URL" | sed -e 's|^.*//||' -e 's|^.*@||' -e 's|:.*||' -e 's|/.*||')
  DB_PORT=5432
fi

if [ -n "$REDIS_URL" ]; then
  # Strips everything up to the protocol and isolates the host domain
  REDIS_HOST=$(echo "$REDIS_URL" | sed -e 's|^.*//||' -e 's|^.*@||' -e 's|:.*||' -e 's|/.*||')
  REDIS_PORT=6379
fi

# Fallback defaults for local development docker containers
DB_HOST=${DB_HOST:-db}
DB_PORT=${DB_PORT:-5432}
REDIS_HOST=${REDIS_HOST:-redis}
REDIS_PORT=${REDIS_PORT:-6379}

# ==========================================================
# 2. RUN PORT CHECKS ONLY IN LOCAL DEVELOPMENT MODE
# ==========================================================
if [ "$ENV" != "prod" ]; then
  echo "⏳ Waiting for local Postgres..."
  while ! nc -z "$DB_HOST" "$DB_PORT"; do sleep 1; done
  echo "✅ Postgres is ready!"

  echo "⏳ Waiting for local Redis..."
  while ! nc -z "$REDIS_HOST" "$REDIS_PORT"; do sleep 1; done
  echo "✅ Redis is ready!"
fi

# ==========================================================
# 3. FAST DATABASE MIGRATIONS
# ==========================================================
echo "📦 Applying database schemas..."
python manage.py migrate --noinput

# ==========================================================
# 4. RUN ASSET MANAGEMENT ONLY IN LOCAL ENVIRONMENTS
# ==========================================================
# Production images already run collectstatic in the Dockerfile, so we skip it on
# boot. Locally the project folder is mounted over /app, hiding those files, so we
# collect them here.
if [ "$ENV" != "prod" ]; then
  echo "📁 Collecting development static files..."
  python manage.py collectstatic --noinput
fi

# Admin account for /admin/, created once from the DJANGO_SUPERUSER_* variables.
if [ "$DJANGO_SUPERUSER_USERNAME" ]; then
  if python manage.py shell -c "import os, sys; from django.contrib.auth import get_user_model; sys.exit(0 if get_user_model().objects.filter(username=os.environ['DJANGO_SUPERUSER_USERNAME']).exists() else 1)" 2>/dev/null; then
    echo "👤 Admin user already exists"
  elif python manage.py createsuperuser --noinput \
      --username "$DJANGO_SUPERUSER_USERNAME" \
      --email "$DJANGO_SUPERUSER_EMAIL" >/dev/null 2>&1; then
    echo "👤 Admin user created"
  else
    # For example, the email already belongs to another account. Boot continues.
    echo "⚠️ Admin user could not be created; check DJANGO_SUPERUSER_USERNAME and DJANGO_SUPERUSER_EMAIL"
  fi
fi

# ==========================================================
# 5. HIGH-SPEED PRODUCTION RUNTIME DEPLOYMENT
# ==========================================================
if [ "$ENV" = "prod" ]; then
  # Process counts are kept small so Celery and Gunicorn fit together in Render's
  # free 512 MB instance. On a bigger plan, raise them with the CELERY_CONCURRENCY
  # and WEB_CONCURRENCY environment variables.
  echo "🚀 Launching background Celery worker..."
  # The ampersand (&) forces Celery to fork to the background so the script can continue
  celery -A config worker --loglevel=info --concurrency "${CELERY_CONCURRENCY:-1}" &

  echo "🚀 Launching high-speed production Gunicorn stack..."
  # Uses Render's dynamic environment port definition variable to handle incoming sync network pools
  exec gunicorn config.wsgi:application \
    --bind 0.0.0.0:$PORT \
    --workers "${WEB_CONCURRENCY:-1}" \
    --threads 4 \
    --timeout 120
else
  echo "🛠 Starting Local Django Dev Server..."
  exec python manage.py runserver 0.0.0.0:8000
fi
