FROM node:24-alpine AS frontend-build
WORKDIR /build/frontend
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm install --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

FROM python:3.13-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PUREVAULT_DATA_DIR=/data \
    PUREVAULT_KEY_FILE=/keys/app.key
WORKDIR /app
RUN groupadd --system purevault && useradd --system --gid purevault --home-dir /app purevault
COPY requirements.txt ./
RUN pip install --no-cache-dir --requirement requirements.txt
COPY manage.py ./
COPY process-supervisor.py ./
COPY purevault/ ./purevault/
COPY vault/ ./vault/
COPY --from=frontend-build /build/frontend/dist ./frontend/dist/
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint
RUN mkdir -p /data /keys /app/staticfiles \
    && chown -R purevault:purevault /data /keys /app \
    && chmod 700 /data /keys \
    && chmod 755 /usr/local/bin/docker-entrypoint
USER purevault
RUN PUREVAULT_DJANGO_SECRET_KEY=build-only-placeholder python manage.py collectstatic --noinput
EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=5s --start-period=20s --retries=6 CMD python -c "import os,urllib.request; host=os.environ.get('PUREVAULT_ALLOWED_HOSTS','localhost').split(',')[0]; urllib.request.urlopen(urllib.request.Request('http://127.0.0.1:8000/health/',headers={'Host':host}),timeout=3)"
ENTRYPOINT ["docker-entrypoint"]
CMD ["gunicorn", "purevault.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "2", "--threads", "2", "--timeout", "30", "--access-logfile", "-", "--error-logfile", "-"]

