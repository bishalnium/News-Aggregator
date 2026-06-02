#!/usr/bin/env bash
set -euo pipefail

if [[ "$(id -u)" -eq 0 ]]; then
  echo "Run this script as a regular user with sudo access, not as root."
  exit 1
fi

echo "[1/4] Installing base packages"
sudo apt-get update
sudo apt-get install -y ca-certificates curl git ufw

echo "[2/4] Installing Docker Engine and Compose plugin"
curl -fsSL https://get.docker.com | sudo sh
sudo apt-get install -y docker-compose-plugin
sudo usermod -aG docker "$USER"
sudo systemctl enable docker
sudo systemctl start docker

echo "[3/4] Configuring firewall"
sudo ufw allow OpenSSH
sudo ufw allow 8000/tcp
sudo ufw --force enable

echo "[4/4] Verification"
docker --version || true
docker compose version || true

echo "Done. Re-login (or run: newgrp docker) before running docker commands without sudo."
