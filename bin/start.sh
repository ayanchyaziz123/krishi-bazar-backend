#!/bin/sh
# Railway start script: prepare the database and static files, then serve.
set -e
python manage.py migrate --no-input
python manage.py collectstatic --no-input
python manage.py bootstrap_site
exec gunicorn backend.wsgi:application --bind "0.0.0.0:${PORT:-8000}" --workers 2 --threads 2 --timeout 60 --log-level info
