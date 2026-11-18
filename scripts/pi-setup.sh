#!/usr/bin/env bash
# One-time setup for the Clippzz Raspberry Pi (64-bit Debian 13 trixie).
# Run on the Pi as the normal user (not root). It asks for your sudo password.
#   bash ~/clippzz/scripts/pi-setup.sh
# Safe to run again: every step checks before it changes anything.
set -euo pipefail

if [ "$(id -u)" -eq 0 ]; then
  echo "Run this as your normal user, not with sudo." >&2
  exit 1
fi
ME="$(id -un)"

echo "==> 1/5 Installing pending OS updates (can take 10-20 minutes)"
sudo apt-get update
sudo apt-get -y full-upgrade

echo "==> 2/5 Installing Docker Engine and the Compose plugin"
if ! command -v docker >/dev/null 2>&1; then
  sudo apt-get install -y ca-certificates curl
  sudo install -m 0755 -d /etc/apt/keyrings
  sudo curl -fsSL https://download.docker.com/linux/debian/gpg -o /etc/apt/keyrings/docker.asc
  sudo chmod a+r /etc/apt/keyrings/docker.asc
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/debian $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
    | sudo tee /etc/apt/sources.list.d/docker.list >/dev/null
  sudo apt-get update
  sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
else
  echo "Docker already installed: $(docker --version)"
fi
sudo systemctl enable --now docker
sudo usermod -aG docker "$ME"

echo "==> 3/5 Creating /data (on the SD card until the USB SSD is mounted there)"
sudo mkdir -p /data
sudo chown "$ME:$ME" /data
mkdir -p /data/sources /data/work /data/clips /data/logs /data/backups /data/library

echo "==> 4/5 Creating the secrets file ~/.clippzz.env (owner-only)"
if [ ! -f "$HOME/.clippzz.env" ]; then
  printf 'ANTHROPIC_API_KEY=\n' > "$HOME/.clippzz.env"
fi
chmod 600 "$HOME/.clippzz.env"

echo "==> 5/5 Done"
echo
echo "Next:"
echo "  1. Put your key after the = sign:  nano ~/.clippzz.env"
echo "  2. Reboot so Docker group membership and kernel updates apply:  sudo reboot"
