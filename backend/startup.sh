#!/bin/bash
# Azure App Service startup script
# App Service sets PORT env var; default to 8000 for local testing
PORT="${PORT:-8000}"
exec gunicorn -w 4 -k uvicorn.workers.UvicornWorker app.main:app \
    --bind "0.0.0.0:${PORT}" \
    --timeout 120 \
    --access-logfile -
