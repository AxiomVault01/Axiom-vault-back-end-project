#!/bin/sh

echo "⏳ Waiting for Postgres..."

while ! nc -z db 5432; do
  sleep 1
done

echo "✅ Postgres is ready!"

echo "⏳ Waiting for Redis..."

while ! nc -z redis 6379; do
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