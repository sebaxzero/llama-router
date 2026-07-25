#!/usr/bin/env bash
# llama-router — stdlib only, no venv needed (Linux / macOS).
cd "$(dirname "$0")"
exec python3 main.py "$@"
