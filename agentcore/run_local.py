#!/usr/bin/env python3
"""Run the Rosetta SDL MCP server locally over streamable-http, for HTTP clients.

rosetta_mcp.py targets AgentCore Runtime: it constructs FastMCP() at import time
without a port, which pins 8000 -- the port the Rosetta API itself uses locally, so
it fails to bind. FASTMCP_PORT does not help, because Settings is built during that
import, before the variable is read. Overriding the instance afterwards does work,
and leaves the deployed module untouched.

Usage:
    MCP_PORT=8901 API_URL=http://localhost:8000 python3 agentcore/run_local.py
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, os.environ.get("ROSETTA_REPO", str(Path(__file__).resolve().parent.parent)))

from agentcore.rosetta_mcp import API_URL, mcp  # noqa: E402  (sys.path set above)

if __name__ == "__main__":
    mcp.settings.host = os.environ.get("MCP_HOST", "127.0.0.1")
    mcp.settings.port = int(os.environ.get("MCP_PORT", "8901"))
    print(
        f"Rosetta MCP (http) on {mcp.settings.host}:{mcp.settings.port} -> API {API_URL}",
        file=sys.stderr,
        flush=True,
    )
    mcp.run(transport="streamable-http")
