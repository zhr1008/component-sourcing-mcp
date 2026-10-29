#!/usr/bin/env sh
set -eu
cd "$(CDPATH= cd "$(dirname "$0")/.." && pwd)"
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -e .
printf 'Installed Component Sourcing MCP. MCP command: %s\n' "$(pwd)/.venv/bin/component-sourcing-mcp"
