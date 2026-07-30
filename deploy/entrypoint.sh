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

echo "Starting Gradio application on ${GRADIO_SERVER_NAME:-127.0.0.1}:${GRADIO_SERVER_PORT:-7860}"
echo "Using data directory: $data_dir"
echo "Using RAG backend: ${RAG_BACKEND:-json}"
exec python ui/gradio_app.py
