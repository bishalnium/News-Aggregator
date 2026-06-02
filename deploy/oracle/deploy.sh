#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT_DIR"

if [[ ! -f .env.oracle ]]; then
  echo "Missing .env.oracle. Create it from .env.oracle.example first."
  exit 1
fi

if [[ ! -f backend/.env ]]; then
  echo "Missing backend/.env. Create it from backend/.env.example first."
  exit 1
fi

echo "Starting/Updating Oracle deployment stack..."
docker compose --env-file .env.oracle -f docker-compose.oracle.yml up -d --build

echo "Services status:"
docker compose --env-file .env.oracle -f docker-compose.oracle.yml ps

BACKEND_PORT_VALUE="$(grep -E '^BACKEND_PORT=' .env.oracle | tail -n 1 | cut -d= -f2- || true)"
BACKEND_PORT_VALUE="${BACKEND_PORT_VALUE:-8000}"

echo "Backend health check:"
curl -fsS "http://127.0.0.1:${BACKEND_PORT_VALUE}/health" || true

echo "Done."
