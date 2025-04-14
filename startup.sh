#!/bin/bash

# Example startup script for Azure App Service or similar platforms

echo "Starting ChatOps Bot..."

# Run database migrations or other setup tasks here if needed

# Start the Uvicorn server using Gunicorn for production
WORKERS=${WEB_CONCURRENCY:-2} # Default to 2 workers if not set
LOG_LEVEL_GUNICORN=$(echo "${LOG_LEVEL:-info}" | tr '[:upper:]' '[:lower:]') # Gunicorn uses lowercase log levels

echo "Using $WORKERS Uvicorn workers."
echo "Gunicorn log level: $LOG_LEVEL_GUNICORN"

# exec gunicorn main:app --workers $WORKERS --worker-class uvicorn.workers.UvicornWorker --bind 0.0.0.0:${PORT:-8000} --log-level $LOG_LEVEL_GUNICORN --access-logfile - --error-logfile - --timeout 120
# Adding timeout to handle potentially slow startup or requests
exec gunicorn main:app --workers $WORKERS --worker-class uvicorn.workers.UvicornWorker --bind 0.0.0.0:${PORT:-8000} --log-level $LOG_LEVEL_GUNICORN --timeout 120

echo "ChatOps Bot start command issued." 