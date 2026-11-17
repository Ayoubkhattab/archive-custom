#!/usr/bin/env bash
# Installs (or updates) the archive from this bundle. No internet needed.
#
#   sudo ./install.sh [address]
#
# address: the IP or host name people will open the archive at. Default:
# this server's first IP address. Only used on the first install, when .env
# is created; after that, edit .env.
#
# Running it again with a newer bundle updates the images and restarts the
# containers; .env, the database and the documents are kept.
set -euo pipefail
cd "$(dirname "$0")"

step() { printf '\n==> %s\n' "$*"; }
die() { echo "ERROR: $*" >&2; exit 1; }

command -v docker >/dev/null || die "Docker is not installed (https://docs.docker.com/engine/install/)."
docker info >/dev/null 2>&1 || die "Cannot talk to Docker. Run with sudo, or start the Docker service."
docker compose version >/dev/null 2>&1 || die "The Docker Compose plugin is missing (package docker-compose-plugin)."
[ "$(uname -m)" = x86_64 ] || die "These images are for x86_64 (amd64) servers, this one is $(uname -m)."

step "Checking the image files"
sha256sum -c --quiet SHA256SUMS || die "Some image files are damaged or incomplete: copy the bundle again."

step "Loading images (this takes a few minutes)"
for file in images/*.tar; do
  echo "  $file"
  docker load -q -i "$file"
done

random() {
  # pipefail off: head closing the pipe early is expected here.
  (set +o pipefail; LC_ALL=C tr -dc 'A-Za-z0-9' </dev/urandom | head -c "$1")
}

if [ ! -f .env ]; then
  step "Creating .env"
  address="${1:-$(hostname -I 2>/dev/null | awk '{print $1}')}"
  [ -n "$address" ] || die "Could not find this server's address: run ./install.sh <ip-or-hostname>"
  cp .env.example .env
  port="$(sed -n 's/^PAPERLESS_PORT=//p' .env)"
  port="${port:-8000}"
  sed -i \
    -e "s#^SECRET_KEY=.*#SECRET_KEY=$(random 64)#" \
    -e "s#^ADMIN_PASSWORD=.*#ADMIN_PASSWORD=$(random 20)#" \
    -e "s#^DB_PASS=.*#DB_PASS=$(random 32)#" \
    -e "s#^PAPERLESS_URL=.*#PAPERLESS_URL=http://$address:$port#" \
    -e "s#^PAPERLESS_ALLOWED_HOSTS=.*#PAPERLESS_ALLOWED_HOSTS=localhost,127.0.0.1,$address#" \
    .env
  chmod 600 .env
fi

grep -q 'CHANGE_ME\|YOUR_UBUNTU_IP' .env && die ".env still has CHANGE_ME / YOUR_UBUNTU_IP values: edit it first."

step "Starting the archive"
docker compose up -d --remove-orphans

port="$(sed -n 's/^PAPERLESS_PORT=//p' .env)"
port="${port:-8000}"
step "Waiting for the web server (the first start takes a few minutes)"
for _ in $(seq 1 120); do
  if curl -fsL -o /dev/null --max-time 5 "http://127.0.0.1:$port/" 2>/dev/null; then
    url="$(sed -n 's/^PAPERLESS_URL=//p' .env)"
    echo
    echo "The archive is running:  $url"
    echo "  user:      $(sed -n 's/^ADMIN_USER=//p' .env)"
    echo "  password:  $(sed -n 's/^ADMIN_PASSWORD=//p' .env)   (also in .env; change it after logging in)"
    exit 0
  fi
  sleep 5
done

docker compose ps
die "The web server did not answer within 10 minutes. See: docker compose logs webserver"
