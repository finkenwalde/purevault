#!/bin/sh
set -eu

umask 077

if [ -z "${PUREVAULT_DJANGO_SECRET_KEY:-}" ]; then
  if [ ! -s /data/django-secret ]; then
    python -c 'import secrets; print(secrets.token_urlsafe(64))' > /data/django-secret
  fi
  PUREVAULT_DJANGO_SECRET_KEY="$(cat /data/django-secret)"
  export PUREVAULT_DJANGO_SECRET_KEY
fi

python manage.py migrate --noinput
PUREVAULT_CLOCK_GENERATION="$(python manage.py initialize_clock)"
export PUREVAULT_CLOCK_GENERATION
exec python process-supervisor.py "$@"

