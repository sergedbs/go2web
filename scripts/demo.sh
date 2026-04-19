#!/usr/bin/env bash
set -euo pipefail

printf '\n== Help ==\n'
uv run go2web -h

printf '\n== URL Fetch with retries ==\n'
uv run go2web -u https://example.com --timeout 8 --retries 1 | head -n 20

printf '\n== Search (DDG) ==\n'
uv run go2web -s "socket programming" | head -n 40

printf '\n== Search (Wikipedia JSON) ==\n'
uv run go2web -s cats --engine wikipedia --json | head -n 60

printf '\n== Cache Controls ==\n'
uv run go2web --clear-cache
