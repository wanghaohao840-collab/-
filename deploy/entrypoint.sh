#!/bin/sh
set -eu

data_dir="${PDF_ASSISTANT_DATA_DIR:-/app/data}"
if [ ! -d "$data_dir" ]; then
    mkdir -p "$data_dir"
fi

probe="$data_dir/.write-probe.$$.tmp"
if ! : > "$probe"; then
    echo "Deployment data directory is not writable: $data_dir" >&2
    exit 1
fi
rm -f "$probe"

echo "Starting unified application on ${APP_HOST:-0.0.0.0}:${APP_PORT:-7860}"
echo "Using data directory: $data_dir"
echo "Using RAG backend: ${RAG_BACKEND:-json}"
exec python -m uvicorn server:app \
  --host "${APP_HOST:-0.0.0.0}" \
  --port "${APP_PORT:-7860}" \
  --workers 1
