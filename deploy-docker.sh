#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_DIR"

if ! command -v docker >/dev/null 2>&1; then
  echo "ERROR: docker is not installed on this server."
  echo "Install Docker first, then rerun: bash deploy-docker.sh"
  exit 1
fi

if docker compose version >/dev/null 2>&1; then
  COMPOSE=(docker compose)
elif command -v docker-compose >/dev/null 2>&1; then
  COMPOSE=(docker-compose)
else
  echo "ERROR: docker compose or docker-compose is not available on this server."
  echo "Install Docker Compose first, then rerun: bash deploy-docker.sh"
  exit 1
fi

if [ ! -f .env ]; then
  cp .env.example .env
  echo "Created .env from .env.example."
  echo "Please edit .env and fill in the real market API settings, then rerun:"
  echo "  vi .env"
  echo "  bash deploy-docker.sh"
  exit 1
fi

if grep -q "your-aliyun-market-kline-url\|your-app-code" .env; then
  echo "ERROR: .env still contains placeholder values."
  echo "Edit .env and fill in ALIYUN_MARKET_KLINE_URL / ALIYUN_MARKET_APPCODE."
  exit 1
fi

echo "Building and starting containers..."
"${COMPOSE[@]}" up -d --build

echo
echo "Container status:"
"${COMPOSE[@]}" ps

echo
echo "Health check:"
if command -v curl >/dev/null 2>&1; then
  curl -fsS http://127.0.0.1/api/health && echo
else
  echo "curl is not installed; open http://SERVER_IP/api/health manually."
fi

echo
echo "Deployment complete."
echo "Open: http://SERVER_IP/"
echo "Logs:"
echo "  ${COMPOSE[*]} logs -f backend"
echo "  ${COMPOSE[*]} logs -f frontend"
