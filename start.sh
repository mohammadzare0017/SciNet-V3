#!/usr/bin/env bash
set -euo pipefail

# Check for docker compose (new syntax)
if ! command -v docker compose &> /dev/null
then
    echo "Docker Compose plugin not found. Installing..."
    sudo apt update && sudo apt install -y docker-compose-plugin
fi

echo "Building Docker image..."
docker compose -f docker-compose.prod.yml build --pull

echo "Starting containers..."
docker compose -f docker-compose.prod.yml up -d

echo "Containers started. Showing status:"
docker compose -f docker-compose.prod.yml ps
