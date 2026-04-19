from __future__ import annotations

import json
import re
from html.parser import HTMLParser
from typing import Iterable

from .http_client import Response

try:
    from bs4 import BeautifulSoup  # type: ignore
except Exception:  # pragma: no cover
    BeautifulSoup = None


CHARSET_RE = re.compile(r"charset=([^;]+)", re.IGNORECASE)
WS_RE = re.compile(r"\s+")
NOISE_LINE_RE = re.compile(
    r"^(v t e|categories:|hidden categories:|retrieved from|this page was last edited|privacy policy|terms of use|authority control|taxon identifiers)",
    re.IGNORECASE,
)
TOKEN_SPLIT_RE = re.compile(r"[^a-z0-9]+")
JUNK_TOKENS = {
    "nav",
    "navbox",
    "menu",
    "sidebar",
    "footer",
    "header",
    "cookie",
    "banner",
    "share",
    "social",
    "promo",
    "breadcrumb",
    "comment",
    "comments",
    "related",
    "toc",
    "taxon",
    "authority",
    "catlinks",
    "metadata",
    "reference",
    "reflist",
}
BOILERPLATE_TAGS = {"div", "section", "aside", "nav", "footer", "header", "ul", "ol", "table"}
PROTECTED_TAGS = {"html", "body", "main", "article"}


def detect_charset(content_type: str, default: str = "utf-8") -> str:
    if not content_type:
        return default
    match = CHARSET_RE.search(content_type)
    if not match:
        return default
    return match.group(1).strip().strip('"').lower() or default


def decode_body(body: bytes, content_type: str) -> str:
    charset = detect_charset(content_type)
    try:
        return body.decode(charset, errors="replace")
    except LookupError:
        return body.decode("utf-8", errors="replace")


class _HTMLTextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._ignored_depth = 0
        self._parts: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:  # noqa: ANN001
        if tag in {"script", "style", "noscript", "nav", "header", "footer", "aside"}:
            self._ignored_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript", "nav", "header", "footer", "aside"} and self._ignored_depth > 0:
            self._ignored_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._ignored_depth > 0:
            return
        text = data.strip()
        if text:
            self._parts.append(text)

    def text(self) -> str:
        return "\n".join(self._parts)


def _html_to_text(html: str) -> str:
    if BeautifulSoup is not None:
        soup = BeautifulSoup(html, "html.parser")
        for tag in soup(["script", "style", "noscript", "svg", "canvas", "template", "iframe"]):
            tag.decompose()

        _remove_boilerplate_nodes(soup)
        main = _pick_main_content_node(soup)
        return _extract_readable_lines(main)

    parser = _HTMLTextExtractor()
    parser.feed(html)
    parser.close()
    lines = [_clean_text(line) for line in parser.text().splitlines()]
    filtered = [line for line in lines if line and not NOISE_LINE_RE.search(line)]
    return "\n".join(filtered)


def _render_json(text: str) -> str:
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return text
    return json.dumps(parsed, indent=2, ensure_ascii=False)


def to_text(response: Response) -> str:
    content_type = response.headers.get("content-type", "")
    body_text = decode_body(response.body, content_type)
    lowered = content_type.lower()

    if "json" in lowered:
        return _render_json(body_text)
    if "html" in lowered:
        return _html_to_text(body_text)

    return body_text


def _iter_class_id_values(node) -> Iterable[str]:  # noqa: ANN001
    if node is None:
        return
    attrs = getattr(node, "attrs", None)
    if not isinstance(attrs, dict):
        return

    node_id = node.get("id")
    if isinstance(node_id, str):
        yield node_id

    classes = node.get("class")
    if isinstance(classes, list):
        for value in classes:
            if isinstance(value, str):
                yield value
    elif isinstance(classes, str):
        yield classes


def _remove_boilerplate_nodes(soup) -> None:  # noqa: ANN001
    for node in soup.find_all(["header", "footer", "nav", "aside", "form", "button", "input"]):
        node.decompose()

    # Use a snapshot list because `decompose()` mutates the tree in-place.
    for node in list(soup.find_all(True)):
        if getattr(node, "attrs", None) is None:
            continue
        if _is_boilerplate_node(node):
            node.decompose()

    # Common Wikipedia-specific noise sections.
    for selector in [
        ".navbox",
        ".vertical-navbox",
        ".metadata",
        ".reference",
        ".reflist",
        ".catlinks",
        ".authority-control",
        ".mw-editsection",
        ".toc",
        "#toc",
        "#footer",
    ]:
        for node in soup.select(selector):
            node.decompose()


def _pick_main_content_node(soup):  # noqa: ANN001
    candidates = []
    for selector in [
        "article",
        "main",
        "[role='main']",
        "#mw-content-text",
        "#content",
        "#main",
        ".entry-content",
        ".post-content",
        ".article-content",
    ]:
        candidates.extend(soup.select(selector))

    if not candidates:
        return soup.body or soup

    best = max(candidates, key=lambda n: len(n.get_text(" ", strip=True)))
    return best


def _clean_text(text: str) -> str:
    return WS_RE.sub(" ", text).strip()


def _extract_readable_lines(node) -> str:  # noqa: ANN001
    lines: list[str] = []
    for tag in node.find_all(["h1", "h2", "h3", "p", "blockquote", "pre"]):
        text = _clean_text(tag.get_text(" ", strip=True))
        if not text:
            continue
        if len(text) < 20 and tag.name == "p":
            continue
        lines.append(text)

    # Fallback if structured extraction yields too little text.
    if len(lines) < 3:
        text = node.get_text(separator="\n")
        compact = [_clean_text(line) for line in text.splitlines()]
        lines = [line for line in compact if line and len(line) > 2]

    deduped: list[str] = []
    seen = set()
    for line in lines:
        if NOISE_LINE_RE.search(line):
            continue
        if line in seen:
            continue
        seen.add(line)
        deduped.append(line)

    # Keep output readable and bounded for large pages.
    joined = "\n".join(deduped)
    if len(joined) > 12000:
        return joined[:12000].rstrip() + "\n...[truncated]..."
    return joined


def _is_boilerplate_node(node) -> bool:  # noqa: ANN001
    name = getattr(node, "name", "")
    if name in PROTECTED_TAGS:
        return False
    if name not in BOILERPLATE_TAGS:
        return False

    values = list(_iter_class_id_values(node))
    if not values:
        return False
    for value in values:
        tokens = [tok for tok in TOKEN_SPLIT_RE.split(value.lower()) if tok]
        if any(tok in JUNK_TOKENS for tok in tokens):
            return True
    return False
