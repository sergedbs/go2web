from __future__ import annotations

import json
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Callable, List, Literal, Optional
from urllib.parse import parse_qs, quote, quote_plus, urljoin, urlparse

from .http_client import Response, ResponseCache, fetch

try:
    from bs4 import BeautifulSoup  # type: ignore
except Exception:  # pragma: no cover
    BeautifulSoup = None

SearchEngine = Literal["ddg", "wikipedia"]


@dataclass
class SearchResult:
    title: str
    url: str
    snippet: str = ""
    rank: int = 0


def _extract_result_url(href: str) -> str:
    href = urljoin("https://duckduckgo.com", href)
    parsed = urlparse(href)
    if parsed.netloc.endswith("duckduckgo.com") and parsed.path.startswith("/l/"):
        query = parse_qs(parsed.query)
        uddg = query.get("uddg")
        if uddg:
            return uddg[0]
    return href


def _class_contains(attrs: dict[str, str], name: str) -> bool:
    value = attrs.get("class", "")
    if not value:
        return False
    return name in value.split()


class _DuckDuckGoParser(HTMLParser):
    def __init__(self, limit: int) -> None:
        super().__init__()
        self.limit = limit
        self.results: List[SearchResult] = []
        self._result_depth = 0
        self._current_url = ""
        self._current_title_parts: List[str] = []
        self._current_snippet_parts: List[str] = []
        self._in_title = False
        self._in_snippet = False

    def _attrs_dict(self, attrs) -> dict[str, str]:  # noqa: ANN001
        return {k: v or "" for k, v in attrs}

    def _finalize_result(self) -> None:
        if not self._current_url:
            return
        title = " ".join(self._current_title_parts).strip()
        if not title:
            return
        snippet = " ".join(self._current_snippet_parts).strip()
        rank = len(self.results) + 1
        self.results.append(SearchResult(title=title, url=self._current_url, snippet=snippet, rank=rank))

    def handle_starttag(self, tag: str, attrs) -> None:  # noqa: ANN001
        if len(self.results) >= self.limit:
            return

        attrs_map = self._attrs_dict(attrs)
        is_result_container = tag == "div" and _class_contains(attrs_map, "result")
        if is_result_container:
            if self._result_depth == 0:
                self._current_url = ""
                self._current_title_parts = []
                self._current_snippet_parts = []
                self._in_title = False
                self._in_snippet = False
            self._result_depth += 1
            return

        if self._result_depth == 0:
            return

        if tag == "a" and _class_contains(attrs_map, "result__a"):
            href = (attrs_map.get("href") or "").strip()
            if href:
                self._current_url = _extract_result_url(href)
                self._in_title = True
            return

        if _class_contains(attrs_map, "result__snippet"):
            self._in_snippet = True

    def handle_endtag(self, tag: str) -> None:
        if self._result_depth == 0:
            return

        if tag == "a" and self._in_title:
            self._in_title = False
        if self._in_snippet and tag in {"a", "div", "span"}:
            self._in_snippet = False

        if tag == "div":
            self._result_depth -= 1
            if self._result_depth == 0:
                self._finalize_result()

    def handle_data(self, data: str) -> None:
        if self._result_depth == 0:
            return
        text = data.strip()
        if not text:
            return
        if self._in_title:
            self._current_title_parts.append(text)
        elif self._in_snippet:
            self._current_snippet_parts.append(text)


def _parse_results_ddg_bs4(html: str, limit: int) -> List[SearchResult]:
    soup = BeautifulSoup(html, "html.parser")
    results: List[SearchResult] = []
    seen_urls: set[str] = set()

    container_selectors = [".result", "article[data-layout='organic']", "article"]
    link_selectors = ["a.result__a", "h2 a", "a[data-testid='result-title-a']", "a"]
    snippet_selectors = [".result__snippet", "a.result__snippet", "[data-result='snippet']", ".snippet"]

    for container_selector in container_selectors:
        for block in soup.select(container_selector):
            link = None
            for link_selector in link_selectors:
                link = block.select_one(link_selector)
                if link is not None:
                    break
            if link is None:
                continue

            href = (link.get("href") or "").strip()
            title = link.get_text(" ", strip=True)
            if not href or not title:
                continue

            url = _extract_result_url(href)
            if not url.startswith(("http://", "https://")):
                continue
            if url in seen_urls:
                continue

            snippet = ""
            for snippet_selector in snippet_selectors:
                snippet_node = block.select_one(snippet_selector)
                if snippet_node is not None:
                    snippet = snippet_node.get_text(" ", strip=True)
                    if snippet:
                        break

            seen_urls.add(url)
            results.append(SearchResult(title=title, url=url, snippet=snippet, rank=len(results) + 1))
            if len(results) >= limit:
                return results

    if results:
        return results[:limit]

    # Final fallback: generic links.
    for link in soup.select("a[href]"):
        href = (link.get("href") or "").strip()
        title = link.get_text(" ", strip=True)
        if not href or not title:
            continue
        url = _extract_result_url(href)
        if not url.startswith(("http://", "https://")):
            continue
        if url in seen_urls:
            continue

        seen_urls.add(url)
        results.append(SearchResult(title=title, url=url, snippet="", rank=len(results) + 1))
        if len(results) >= limit:
            break

    return results[:limit]


def _parse_results_ddg(html: str, limit: int) -> List[SearchResult]:
    if BeautifulSoup is not None:
        parsed = _parse_results_ddg_bs4(html, limit)
        if parsed:
            return parsed

    parser = _DuckDuckGoParser(limit=limit)
    parser.feed(html)
    parser.close()
    return parser.results[:limit]


def _parse_results_wikipedia(json_payload: str, limit: int) -> List[SearchResult]:
    try:
        payload = json.loads(json_payload)
    except json.JSONDecodeError:
        return []

    entries = payload.get("query", {}).get("search", [])
    if not isinstance(entries, list):
        return []

    results: List[SearchResult] = []
    for entry in entries:
        title = str(entry.get("title", "")).strip()
        snippet_html = str(entry.get("snippet", "")).strip()
        if not title:
            continue

        if BeautifulSoup is not None:
            snippet = BeautifulSoup(snippet_html, "html.parser").get_text(" ", strip=True)
        else:
            snippet = snippet_html

        url = f"https://en.wikipedia.org/wiki/{quote(title.replace(' ', '_'))}"
        results.append(SearchResult(title=title, url=url, snippet=snippet, rank=len(results) + 1))
        if len(results) >= limit:
            break

    return results


def _search_ddg(
    term: str,
    *,
    limit: int,
    fetch_fn: Callable[..., Response],
    cache: Optional[ResponseCache],
    timeout: float,
    retries: int,
) -> List[SearchResult]:
    query = quote_plus(term)
    url = f"https://duckduckgo.com/html/?q={query}"
    response = fetch_fn(url, cache=cache, timeout=timeout, retries=retries)
    html = response.body.decode("utf-8", errors="replace")
    return _parse_results_ddg(html, limit=limit)


def _search_wikipedia(
    term: str,
    *,
    limit: int,
    fetch_fn: Callable[..., Response],
    cache: Optional[ResponseCache],
    timeout: float,
    retries: int,
) -> List[SearchResult]:
    query = quote_plus(term)
    url = (
        "https://en.wikipedia.org/w/api.php"
        f"?action=query&list=search&format=json&utf8=1&srlimit={max(1, min(limit, 50))}&srsearch={query}"
    )
    response = fetch_fn(url, cache=cache, timeout=timeout, retries=retries)
    payload = response.body.decode("utf-8", errors="replace")
    return _parse_results_wikipedia(payload, limit=limit)


def search(
    term: str,
    *,
    limit: int = 10,
    engine: SearchEngine = "ddg",
    timeout: float = 10.0,
    retries: int = 0,
    fetcher: Optional[Callable[..., Response]] = None,
    cache: Optional[ResponseCache] = None,
) -> List[SearchResult]:
    fetch_fn = fetcher or fetch

    if engine == "wikipedia":
        return _search_wikipedia(
            term,
            limit=limit,
            fetch_fn=fetch_fn,
            cache=cache,
            timeout=timeout,
            retries=retries,
        )

    return _search_ddg(
        term,
        limit=limit,
        fetch_fn=fetch_fn,
        cache=cache,
        timeout=timeout,
        retries=retries,
    )
