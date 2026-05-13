# ClawHub Plugin Marketplace

Relay Teams can list, search, and install ClawHub `code-plugin` and
`bundle-plugin` packages through the plugin marketplace layer.

## CLI

List available packages:

```powershell
uv run --extra dev relay-teams plugin list --available --marketplace clawhub --marketplace-provider clawhub
```

Search ClawHub:

```powershell
uv run --extra dev relay-teams plugin search market
```

Install a package:

```powershell
uv run --extra dev relay-teams plugin install clawhub:package-name
```

Set `--marketplace-source` when using a non-default ClawHub base URL.

## Install Policy

ClawHub installs use a conservative backend default policy. Lightweight browse
responses are optimized for fast discovery and may not include enough release
metadata to mark every blocked version. Detail-aware browse, search, inspect,
install, and update requests apply the policy when release metadata is
available; blocked versions include `unsupported_reason` and cannot be
installed.

Default policy:

- community or non-official channels are blocked
- packages that declare code execution are blocked
- packages without a clean scan are blocked
- artifact digest metadata is required

To relax the policy for one CLI install, pass explicit override flags:

```powershell
uv run --extra dev relay-teams plugin install clawhub:package-name --allow-executes-code --allow-missing-digest
```

Available one-shot overrides are `--allow-community-plugins`,
`--allow-executes-code`, `--allow-missing-digest`, and
`--allow-unclean-scan`.

The web UI currently sends the one-shot `allow_missing_digest` override for
ClawHub marketplace installs. This keeps directly mappable ClawHub packages
installable when ClawHub does not publish artifact digest metadata. Other policy
overrides remain off unless explicitly requested through API or CLI.

The persistent policy file lives at
`<app-config>/plugins/marketplace-policy.json`:

```json
{
  "allow_community_plugins": false,
  "allow_executes_code": false,
  "require_digest": true,
  "allow_unclean_scan": false
}
```

## Compatibility

Relay Teams supports ClawHub packages that include Relay-mappable plugin
components such as skills, commands, hooks, MCP servers, roles, or Claude
plugin fallback metadata. OpenClaw native-only runtime extension packages are
rejected because Relay Teams does not execute OpenClaw JavaScript or TypeScript
runtime extensions.

The settings UI only lists ClawHub packages reported as direct compatibility.
Partially compatible, unknown, and OpenClaw-native-only packages are ignored by
the UI instead of being shown as unavailable.

The installer verifies archive digest metadata when ClawHub provides SHA or npm
SRI integrity values. Packages that execute code, come from community channels,
use legacy ZIP artifacts, or lack digest metadata are surfaced with marketplace
warnings. The install policy decides which warnings are blocking.

## Live Verification

Live ClawHub tests are opt-in because they require outbound network access and,
in some environments, authenticated proxy configuration.

```powershell
$env:RELAY_TEAMS_RUN_CLAWHUB_LIVE_TESTS = "1"
uv run --extra dev pytest -q tests/integration_tests/cli/test_clawhub_plugins_live.py
```

To include artifact installation:

```powershell
$env:RELAY_TEAMS_CLAWHUB_LIVE_INSTALL_PACKAGE = "package-name"
uv run --extra dev pytest -q tests/integration_tests/cli/test_clawhub_plugins_live.py
```
