#!/bin/bash

# 1. Run database migrations safely during startup
python manage.py migrate --noinput

# 2. Start the Celery worker in the background (Replace 'my_project' with your Django project name)
celery -A my_project worker --loglevel=info &

# 3. Start Gunicorn web server in the foreground
gunicorn my_project.wsgi:application --bind 0.0.0.0:$PORT
