#!/bin/sh
set -e

# ==========================================================
# 1. LIGHTWEIGHT HOST PARSING FOR RENDER / PRODUCTION
# ==========================================================
if [ -n "$DATABASE_URL" ]; then
  # Native shell manipulation (100x faster than spawning a Python script)
  DB_HOST=$(echo "$DATABASE_URL" | sed -e 's@^.*://@@' -e 's@:.*@@' -e 's@/.*@@' -e 's@^.*外观@@')
  DB_PORT=5432
fi

if [ -n "$REDIS_URL" ]; then
  # Clean regex stripping to isolate the Redis host domain
  REDIS_HOST=$(echo "$REDIS_URL" | sed -e 's@^.*://@@' -e 's@:.*@@' -e 's@/.*@@' -e 's@^.*外观@@')
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
# We already set Render to process collectstatic during the Build Phase, 
# so we skip this time-consuming task on container boot.
if [ "$ENV" != "prod" ]; then
  echo "📁 Collecting development static files..."
  python manage.py collectstatic --noinput
fi

# Optional development superuser creation
if [ "$DJANGO_SUPERUSER_USERNAME" ]; then
  echo "👤 Checking superuser parameters..."
  python manage.py createsuperuser \
    --noinput \
    --username "$DJANGO_SUPERUSER_USERNAME" \
    --email "$DJANGO_SUPERUSER_EMAIL" || true
fi

# ==========================================================
# 5. HIGH-SPEED PRODUCTION RUNTIME DEPLOYMENT
# ==========================================================
if [ "$ENV" = "prod" ]; then
  echo "🚀 Launching high-speed production Gunicorn stack..."
  # Explicitly passes multi-threaded execution flags to handle incoming sync network pools
  exec gunicorn config.wsgi:application \
    --bind 0.0.0.0:8000 \
    --workers 2 \
    --threads 2 \
    --timeout 120
else
  echo "🛠 Starting Local Django Dev Server..."
  exec python manage.py runserver 0.0.0.0:8000
fi
