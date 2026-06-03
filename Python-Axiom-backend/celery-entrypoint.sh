#!/bin/sh

echo "Waiting for DB..."

DB_HOST=${DB_HOST:-db}
DB_PORT=${DB_PORT:-5432}

while ! nc -z "$DB_HOST" "$DB_PORT"; do
  sleep 1
done

echo "Waiting for migrations..."

sleep 5

exec "$@"