FROM python:3.11

WORKDIR /app

RUN apt-get update && apt-get install -y netcat-openbsd

COPY requirements.txt .
RUN pip install --upgrade pip
RUN pip install -r requirements.txt

COPY . .

# Collect admin and Swagger static files into the image; WhiteNoise serves them in production.
RUN python manage.py collectstatic --noinput

COPY entrypoint.sh /entrypoint.sh
COPY celery-entrypoint.sh /celery-entrypoint.sh

RUN sed -i 's/\r//' /entrypoint.sh && chmod +x /entrypoint.sh
RUN sed -i 's/\r//' /celery-entrypoint.sh && chmod +x /celery-entrypoint.sh

# Change CMD to ENTRYPOINT so this script ALWAYS runs first
ENTRYPOINT ["/entrypoint.sh"]
