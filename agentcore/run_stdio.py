#!/usr/bin/env python3
"""Run the Rosetta SDL MCP server over stdio, for GUI clients that spawn a subprocess.

Amazon Quick Suite and Claude Desktop take a JSON config with command/args/env and
launch a child process, speaking MCP over stdin/stdout. rosetta_mcp.py hardcodes
transport="streamable-http" in its main(), so it cannot serve those clients directly.
This imports the same FastMCP instance -- all 9 tools, unchanged -- over stdio.

Nothing may be written to stdout: it carries the protocol. Logging in rosetta_mcp.py
goes to stderr, which is safe.

Configure the client with absolute paths; GUI apps do not inherit the shell PATH:

    {
      "command": "/absolute/path/to/python3",
      "args": ["/absolute/path/to/rosetta-sdl/agentcore/run_stdio.py"],
      "env": {"API_URL": "http://localhost:8000",
              "ROSETTA_REPO": "/absolute/path/to/rosetta-sdl"}
    }
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, os.environ.get("ROSETTA_REPO", str(Path(__file__).resolve().parent.parent)))

from agentcore.rosetta_mcp import mcp  # noqa: E402  (sys.path set above)

if __name__ == "__main__":
    mcp.run(transport="stdio")
