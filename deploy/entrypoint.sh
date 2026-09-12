#!/bin/sh
set -eu
# Seed only a new volume; subsequent deployments retain the database.
if [ ! -f /app/data/watchdog.sqlite ]; then
    cp /opt/watchdog-demo.sqlite /app/data/watchdog.sqlite
fi
exec "$@"
