#!/bin/sh

echo "Waiting for DB..."

while ! nc -z db 5432; do
  sleep 1
done

echo "Waiting for migrations..."

sleep 5

exec "$@"