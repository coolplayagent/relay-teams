#!/bin/sh
# Entrypoint for agent-teams eval containers.
#
# When a host config directory is bind-mounted (read-only) at
# /tmp/agent-config-host, this script copies its contents into the
# real config location and removes any SQLite database files so that
# each container starts with its own independent database.
#
# Usage (set by DockerWorkspaceSetup automatically):
#   docker run ... -v /host/config:/tmp/agent-config-host:ro <image> \
#       /opt/agent-runtime/eval-entrypoint.sh \
#       /opt/agent-runtime/venv/bin/agent-teams server start ...

CONFIG_STAGING="/tmp/agent-config-host"
CONFIG_TARGET="/root/.config/agent-teams"

if [ -d "$CONFIG_STAGING" ]; then
    mkdir -p "$CONFIG_TARGET"
    cp -a "$CONFIG_STAGING"/. "$CONFIG_TARGET"/
    rm -f "$CONFIG_TARGET"/*.db "$CONFIG_TARGET"/*.db-wal "$CONFIG_TARGET"/*.db-shm
fi

exec "$@"
