# go2web

`go2web` is a command-line tool for browsing the web from terminal.
It performs HTTP/HTTPS requests over raw TCP sockets, supports quick search, and prints readable output instead of raw HTML.

## What it does

- Fetches a URL and shows readable content
- Searches the web and shows top results
- Opens a selected result directly from CLI
- Handles redirects automatically
- Uses local cache for repeated requests
- Supports both text/HTML and JSON responses

## Install

```bash
uv sync
```

## Quick start

```bash
uv run go2web -h
uv run go2web -u https://example.com
uv run go2web -s cats
uv run go2web -s cats --open 1
```

## Demo

![go2web demo](demo.gif)

## Main commands

- `-u, --url <URL>` fetch and print content from a URL
- `-s, --search <terms...>` search and print top 10 results
- `--open <N>` open search result number `N`
- `--interactive` choose result with arrow keys

## Useful options

- `--engine {ddg,wikipedia}` choose search backend
- `--json` print search results in JSON format
- `--timeout <seconds>` set request timeout
- `--retries <n>` retry timeout/connect failures
- `--cache-ttl <seconds>` set cache TTL
- `--no-cache` disable cache for current run
- `--cache-dir <path>` use custom cache location
- `--clear-cache` clear stored cache entries

## Examples

```bash
# URL fetch
uv run go2web -u https://example.com

# Search and open first result
uv run go2web -s "socket programming" --open 1

# Search using Wikipedia backend with JSON output
uv run go2web -s cats --engine wikipedia --json

# Clear cache
uv run go2web --clear-cache
```

## Run tests

```bash
uv run pytest
```

## Demo script

```bash
bash scripts/demo.sh
```
