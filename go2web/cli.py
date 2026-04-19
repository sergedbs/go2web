from __future__ import annotations

import curses
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

import typer

from .cache import DiskCache
from .http_client import HTTPError, ResponseCache, fetch
from .render import to_text
from .search import SearchEngine, SearchResult, search

app = typer.Typer(
    add_completion=False,
    invoke_without_command=True,
    no_args_is_help=False,
    context_settings={"help_option_names": ["-h", "--help"]},
)


def _print_search_results(results: List[SearchResult]) -> None:
    for item in results:
        rank = item.rank if item.rank > 0 else results.index(item) + 1
        typer.echo(f"{rank}. {item.title}")
        typer.echo(f"   {item.url}")
        if item.snippet:
            typer.echo(f"   {item.snippet}")


def _interactive_pick(results: List[SearchResult]) -> Optional[int]:
    def _add_line(stdscr: "curses._CursesWindow", y: int, x: int, text: str, width: int) -> None:
        if y < 0:
            return
        try:
            stdscr.addnstr(y, x, text, max(0, width - x - 1))
        except curses.error:
            return

    def _picker(stdscr: "curses._CursesWindow") -> int:
        curses.curs_set(0)
        stdscr.keypad(True)
        selected = 0

        while True:
            stdscr.erase()
            height, width = stdscr.getmaxyx()
            list_top = 2
            status_line = height - 1
            max_rows = max(1, status_line - list_top)
            row_span = 2 if width >= 70 else 1
            page_size = max(1, max_rows // row_span)
            page_start = (selected // page_size) * page_size
            page_end = min(len(results), page_start + page_size)

            _add_line(stdscr, 0, 0, "Select result (UP/DOWN, PgUp/PgDn, Enter=open, q=cancel)", width)
            _add_line(stdscr, 1, 0, f"Showing {page_start + 1}-{page_end} of {len(results)}", width)

            y = list_top
            for idx in range(page_start, page_end):
                item = results[idx]
                marker = ">" if idx == selected else " "
                title = f"{marker} {idx + 1}. {item.title}"
                _add_line(stdscr, y, 0, title, width)
                y += 1

                if row_span == 2:
                    snippet = item.snippet or item.url
                    _add_line(stdscr, y, 3, snippet, width)
                    y += 1

            status = f"[{selected + 1}/{len(results)}] Enter=open  q=cancel"
            _add_line(stdscr, status_line, 0, status, width)
            stdscr.refresh()

            key = stdscr.getch()
            if key in (curses.KEY_UP, ord("k")):
                selected = (selected - 1) % len(results)
            elif key in (curses.KEY_DOWN, ord("j")):
                selected = (selected + 1) % len(results)
            elif key == curses.KEY_NPAGE:
                selected = min(len(results) - 1, selected + page_size)
            elif key == curses.KEY_PPAGE:
                selected = max(0, selected - page_size)
            elif key in (10, 13, curses.KEY_ENTER):
                return selected + 1
            elif key in (ord("q"), 27):
                return 0

    try:
        index = curses.wrapper(_picker)
    except curses.error:
        return None

    if index <= 0:
        return None
    return index


def _prompt_pick(results: List[SearchResult]) -> Optional[int]:
    while True:
        try:
            raw = input("Enter result number to open (blank to skip): ").strip()
        except EOFError:
            return None
        if not raw:
            return None

        try:
            index = int(raw)
        except ValueError:
            typer.echo("Invalid number.", err=True)
            continue

        if not 1 <= index <= len(results):
            typer.echo(f"Please choose a number between 1 and {len(results)}.", err=True)
            continue
        return index


def _fetch_and_print(
    url: str,
    cache: Optional[ResponseCache],
    max_redirects: int,
    timeout: float,
    retries: int,
) -> None:
    response = fetch(url, cache=cache, max_redirects=max_redirects, timeout=timeout, retries=retries)
    if cache is not None and response.from_cache:
        typer.echo(f"[cache] HIT {url}", err=True)

    typer.echo(to_text(response))


def _json_payload(query: str, engine: SearchEngine, results: List[SearchResult]) -> str:
    payload = {
        "query": query,
        "engine": engine,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "results": [
            {
                "rank": item.rank if item.rank > 0 else idx,
                "title": item.title,
                "url": item.url,
                "snippet": item.snippet,
            }
            for idx, item in enumerate(results, start=1)
        ],
    }
    return json.dumps(payload, indent=2, ensure_ascii=False)


@app.callback()
def run(
    ctx: typer.Context,
    url: Optional[str] = typer.Option(None, "-u", "--url", help="Fetch and print a URL."),
    search_terms: Optional[List[str]] = typer.Option(
        None,
        "-s",
        "--search",
        help="Search the web and print top results.",
    ),
    open_index: Optional[int] = typer.Option(None, "--open", help="Open search result number N."),
    interactive: bool = typer.Option(
        False,
        "--interactive",
        help="Choose search result with arrow keys and Enter.",
    ),
    cache_ttl: int = typer.Option(600, "--cache-ttl", help="Disk cache TTL in seconds (default: 600)."),
    max_redirects: int = typer.Option(5, "--max-redirects", help="Maximum redirects to follow (default: 5)."),
    timeout: float = typer.Option(10.0, "--timeout", help="Request timeout in seconds (default: 10)."),
    retries: int = typer.Option(0, "--retries", help="Retry count for timeout/connect errors (default: 0)."),
    engine: SearchEngine = typer.Option("ddg", "--engine", help="Search engine: ddg | wikipedia."),
    json_output: bool = typer.Option(False, "--json", help="Output search results as JSON."),
    no_cache: bool = typer.Option(False, "--no-cache", help="Disable cache for this run."),
    cache_dir: Optional[Path] = typer.Option(None, "--cache-dir", help="Override cache directory."),
    clear_cache: bool = typer.Option(False, "--clear-cache", help="Clear cache files before execution."),
) -> None:
    if ctx.invoked_subcommand is not None:
        return

    if timeout <= 0:
        typer.echo("--timeout must be > 0", err=True)
        raise typer.Exit(code=2)

    if retries < 0:
        typer.echo("--retries must be >= 0", err=True)
        raise typer.Exit(code=2)

    if cache_ttl < 0:
        typer.echo("--cache-ttl must be >= 0", err=True)
        raise typer.Exit(code=2)

    if max_redirects < 0:
        typer.echo("--max-redirects must be >= 0", err=True)
        raise typer.Exit(code=2)

    if open_index is not None and open_index < 1:
        typer.echo("--open must be >= 1", err=True)
        raise typer.Exit(code=2)

    if url is None and not search_terms and not clear_cache:
        typer.echo(ctx.get_help())
        raise typer.Exit(code=0)

    if url is not None and search_terms:
        typer.echo("Choose only one of -u/--url or -s/--search.", err=True)
        raise typer.Exit(code=2)

    if url is not None and engine != "ddg":
        typer.echo("--engine is only valid with -s/--search", err=True)
        raise typer.Exit(code=2)

    if open_index is not None and not search_terms:
        typer.echo("--open can only be used with -s/--search", err=True)
        raise typer.Exit(code=2)

    if interactive and not search_terms:
        typer.echo("--interactive can only be used with -s/--search", err=True)
        raise typer.Exit(code=2)

    if json_output and not search_terms:
        typer.echo("--json can only be used with -s/--search", err=True)
        raise typer.Exit(code=2)

    if json_output and (interactive or open_index is not None):
        typer.echo("--json cannot be combined with --interactive or --open", err=True)
        raise typer.Exit(code=2)

    disk_cache = DiskCache(cache_dir=cache_dir, ttl_seconds=cache_ttl)
    if clear_cache:
        removed = disk_cache.clear()
        typer.echo(f"Cleared {removed} cache entries.", err=True)

    if url is None and not search_terms and clear_cache:
        raise typer.Exit(code=0)

    cache: Optional[ResponseCache] = None if no_cache else disk_cache

    try:
        if url:
            _fetch_and_print(url, cache, max_redirects, timeout, retries)
            raise typer.Exit(code=0)

        term = " ".join(search_terms or [])
        results = search(
            term,
            limit=10,
            engine=engine,
            timeout=timeout,
            retries=retries,
            cache=cache,
        )

        if json_output:
            typer.echo(_json_payload(term, engine, results))
            raise typer.Exit(code=0)

        if not results:
            typer.echo("No results found.")
            raise typer.Exit(code=0)

        _print_search_results(results)

        selected_index = open_index
        if selected_index is None and interactive:
            if sys.stdin.isatty() and sys.stdout.isatty():
                selected_index = _interactive_pick(results)
            if selected_index is None:
                selected_index = _prompt_pick(results)

        if selected_index is None:
            raise typer.Exit(code=0)

        if not 1 <= selected_index <= len(results):
            typer.echo(f"--open must be between 1 and {len(results)}", err=True)
            raise typer.Exit(code=2)

        chosen = results[selected_index - 1]
        typer.echo(f"\nOpening result #{selected_index}: {chosen.url}\n", err=True)
        _fetch_and_print(chosen.url, cache, max_redirects, timeout, retries)
        raise typer.Exit(code=0)
    except HTTPError as exc:
        typer.echo(f"Request failed: {exc}", err=True)
        raise typer.Exit(code=1)
    except KeyboardInterrupt:
        typer.echo("Interrupted.", err=True)
        raise typer.Exit(code=130)


def main() -> None:
    app()
