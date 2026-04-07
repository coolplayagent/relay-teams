from __future__ import annotations

from relay_teams.tools.web_tools.webfetch import register as register_webfetch
from relay_teams.tools.web_tools.websearch import register as register_websearch

TOOLS = {
    "webfetch": register_webfetch,
    "websearch": register_websearch,
}
