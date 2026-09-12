#!/usr/bin/env bash
set -Eeuo pipefail
cd "$HOME/watchdog"
region=$1
image=$2
[[ "$region" =~ ^[a-z]{2}-[a-z]+-[0-9]+$ ]]
[[ "$image" =~ ^861560493301\.dkr\.ecr\.[a-z0-9-]+\.amazonaws\.com/[a-z0-9/_-]+:[a-f0-9]{40}$ ]]
command -v aws >/dev/null
docker compose version
# Use the EC2 instance role, not GitHub's temporary credentials.
export DOCKER_CONFIG
DOCKER_CONFIG=$(mktemp -d)
trap 'rm -rf "$DOCKER_CONFIG"' EXIT
aws ecr get-login-password --region "$region" | docker login --username AWS --password-stdin "${image%%/*}"
export APP_IMAGE="$image"
docker compose pull
# Pull succeeds before touching the running stack. Recreate nginx to refresh DNS.
if ! docker compose up -d --force-recreate --wait --wait-timeout 180; then
    docker compose ps
    docker compose logs --tail=100
    exit 1
fi
curl --fail --silent --show-error http://127.0.0.1:12020/api/v1/health
printf 'APP_IMAGE=%s\n' "$image" > .image.env
