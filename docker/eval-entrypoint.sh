#!/bin/sh
# Entrypoint for agent-teams eval containers.
#
# When a host config directory is bind-mounted (read-only) at
# /tmp/agent-config-host, this script copies only a small whitelist of
# runtime config files into the real config location. This avoids
# leaking host-local state such as logs while still allowing eval
# containers to reuse the model/role/skill setup they need.
#
# Usage (set by DockerWorkspaceSetup automatically):
#   docker run ... -v /host/config:/tmp/agent-config-host:ro <image> \
#       /opt/agent-runtime/eval-entrypoint.sh \
#       /opt/agent-runtime/bin/agent-teams server start ...

CONFIG_STAGING="${AGENT_TEAMS_CONFIG_STAGING:-/tmp/agent-config-host}"
CONFIG_TARGET="${AGENT_TEAMS_CONFIG_TARGET:-/root/.config/agent-teams}"

if [ -d "$CONFIG_STAGING" ]; then
    mkdir -p "$CONFIG_TARGET"
    for entry in model.json notifications.json .env mcp.json logger.ini; do
        if [ -f "$CONFIG_STAGING/$entry" ]; then
            cp -a "$CONFIG_STAGING/$entry" "$CONFIG_TARGET/$entry"
        fi
    done

    for entry in roles skills; do
        if [ -d "$CONFIG_STAGING/$entry" ]; then
            cp -a "$CONFIG_STAGING/$entry" "$CONFIG_TARGET/"
        fi
    done

    rm -f "$CONFIG_TARGET"/*.db "$CONFIG_TARGET"/*.db-wal "$CONFIG_TARGET"/*.db-shm
fi

exec "$@"
