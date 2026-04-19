from __future__ import annotations

import gzip
import socket
import ssl
import time
import zlib
from dataclasses import dataclass
from typing import Dict, Optional, Protocol, Tuple
from urllib.parse import urljoin, urlsplit


DEFAULT_ACCEPT = "text/html, application/json;q=0.9, */*;q=0.8"


@dataclass
class Response:
    status_code: int
    reason: str
    headers: Dict[str, str]
    body: bytes
    url: str
    from_cache: bool = False


class HTTPError(Exception):
    pass


class ResponseCache(Protocol):
    def get(self, url: str) -> Optional[Response]: ...

    def set(self, url: str, response: Response) -> None: ...


def _normalize_headers(headers: Dict[str, str]) -> Dict[str, str]:
    return {k.lower(): v for k, v in headers.items()}


def _parse_url(url: str) -> Tuple[str, str, int, str]:
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"}:
        raise HTTPError(f"Unsupported URL scheme: {parsed.scheme!r}")
    if not parsed.hostname:
        raise HTTPError("URL is missing a hostname")

    port = parsed.port
    if port is None:
        port = 443 if parsed.scheme == "https" else 80

    path = parsed.path or "/"
    if parsed.query:
        path = f"{path}?{parsed.query}"

    return parsed.scheme, parsed.hostname, port, path


def _build_request(host: str, path: str) -> bytes:
    request_lines = [
        f"GET {path} HTTP/1.1",
        f"Host: {host}",
        "User-Agent: go2web/0.2",
        f"Accept: {DEFAULT_ACCEPT}",
        "Accept-Encoding: gzip, deflate",
        "Connection: close",
        "",
        "",
    ]
    return "\r\n".join(request_lines).encode("ascii")


def _parse_headers(header_bytes: bytes) -> Tuple[int, str, Dict[str, str]]:
    text = header_bytes.decode("iso-8859-1")
    lines = text.split("\r\n")
    if not lines:
        raise HTTPError("parse/decode: empty header block")

    status_parts = lines[0].split(" ", 2)
    if len(status_parts) < 2:
        raise HTTPError(f"parse/decode: invalid status line {lines[0]!r}")

    try:
        status_code = int(status_parts[1])
    except ValueError as exc:
        raise HTTPError(f"parse/decode: invalid status code {lines[0]!r}") from exc

    reason = status_parts[2] if len(status_parts) > 2 else ""

    headers: Dict[str, str] = {}
    for line in lines[1:]:
        if not line or ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip().lower()
        value = value.strip()
        if key in headers:
            headers[key] = f"{headers[key]}, {value}"
        else:
            headers[key] = value

    return status_code, reason, headers


def _recv_until(sock: socket.socket, marker: bytes) -> Tuple[bytes, bytes]:
    buffer = b""
    while marker not in buffer:
        chunk = sock.recv(4096)
        if not chunk:
            break
        buffer += chunk

    if marker not in buffer:
        raise HTTPError("parse/decode: incomplete response headers")

    header_bytes, remainder = buffer.split(marker, 1)
    return header_bytes, remainder


def _recv_exact(sock: socket.socket, initial: bytes, size: int) -> bytes:
    data = initial
    while len(data) < size:
        chunk = sock.recv(4096)
        if not chunk:
            break
        data += chunk
    return data[:size]


def _recv_all(sock: socket.socket, initial: bytes) -> bytes:
    data = initial
    while True:
        chunk = sock.recv(4096)
        if not chunk:
            break
        data += chunk
    return data


def _decode_chunked(sock: socket.socket, initial: bytes) -> bytes:
    buffer = initial
    decoded = bytearray()

    while True:
        while b"\r\n" not in buffer:
            chunk = sock.recv(4096)
            if not chunk:
                raise HTTPError("parse/decode: EOF while reading chunk length")
            buffer += chunk

        line, buffer = buffer.split(b"\r\n", 1)
        line = line.split(b";", 1)[0].strip()

        try:
            chunk_len = int(line, 16)
        except ValueError as exc:
            raise HTTPError(f"parse/decode: invalid chunk size {line!r}") from exc

        if chunk_len == 0:
            while b"\r\n\r\n" not in buffer:
                chunk = sock.recv(4096)
                if not chunk:
                    return bytes(decoded)
                buffer += chunk
            return bytes(decoded)

        while len(buffer) < chunk_len + 2:
            chunk = sock.recv(4096)
            if not chunk:
                raise HTTPError("parse/decode: EOF while reading chunk data")
            buffer += chunk

        decoded.extend(buffer[:chunk_len])
        buffer = buffer[chunk_len + 2 :]


def _decode_content_encoding(body: bytes, content_encoding: str) -> bytes:
    enc = content_encoding.lower()
    if "gzip" in enc:
        return gzip.decompress(body)
    if "deflate" in enc:
        try:
            return zlib.decompress(body)
        except zlib.error:
            return zlib.decompress(body, -zlib.MAX_WBITS)
    return body


def _read_response(sock: socket.socket) -> Tuple[int, str, Dict[str, str], bytes]:
    headers_raw, remainder = _recv_until(sock, b"\r\n\r\n")
    status_code, reason, headers = _parse_headers(headers_raw)
    headers = _normalize_headers(headers)

    transfer_encoding = headers.get("transfer-encoding", "")
    content_length = headers.get("content-length")

    if "chunked" in transfer_encoding.lower():
        body = _decode_chunked(sock, remainder)
    elif content_length is not None:
        try:
            expected = int(content_length)
        except ValueError:
            body = _recv_all(sock, remainder)
        else:
            body = _recv_exact(sock, remainder, expected)
    else:
        body = _recv_all(sock, remainder)

    content_encoding = headers.get("content-encoding", "")
    if content_encoding:
        try:
            body = _decode_content_encoding(body, content_encoding)
            headers.pop("content-encoding", None)
        except Exception as exc:  # pragma: no cover
            raise HTTPError(f"parse/decode: unable to decode content encoding ({exc})") from exc

    return status_code, reason, headers, body


def _open_socket(scheme: str, host: str, port: int, timeout: float) -> socket.socket:
    raw = socket.create_connection((host, port), timeout=timeout)
    if scheme != "https":
        return raw

    context = ssl.create_default_context()
    tls_sock = context.wrap_socket(raw, server_hostname=host)
    return tls_sock


def _wrap_network_error(exc: Exception, url: str) -> HTTPError:
    if isinstance(exc, socket.timeout):
        return HTTPError(f"timeout: request to {url} exceeded timeout")
    if isinstance(exc, socket.gaierror):
        return HTTPError(f"dns/connect: unable to resolve/connect for {url} ({exc})")
    if isinstance(exc, ssl.SSLError):
        return HTTPError(f"dns/connect: TLS handshake failed for {url} ({exc})")
    if isinstance(exc, OSError):
        return HTTPError(f"dns/connect: network error for {url} ({exc})")
    return HTTPError(f"dns/connect: request failed for {url} ({exc})")


def _is_retryable(exc: Exception) -> bool:
    return isinstance(exc, (socket.timeout, socket.gaierror, ssl.SSLError, ConnectionError, OSError))


def fetch(
    url: str,
    *,
    timeout: float = 10.0,
    max_redirects: int = 5,
    retries: int = 0,
    cache: Optional[ResponseCache] = None,
) -> Response:
    current_url = url
    retries = max(0, retries)

    for _ in range(max_redirects + 1):
        if cache is not None:
            cached = cache.get(current_url)
            if cached is not None:
                return cached

        scheme, host, port, path = _parse_url(current_url)
        request = _build_request(host, path)

        status_code = 0
        reason = ""
        headers: Dict[str, str] = {}
        body = b""
        last_exc: Optional[Exception] = None

        for attempt in range(retries + 1):
            try:
                with _open_socket(scheme, host, port, timeout) as sock:
                    sock.sendall(request)
                    status_code, reason, headers, body = _read_response(sock)
                last_exc = None
                break
            except HTTPError:
                raise
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                if not _is_retryable(exc) or attempt >= retries:
                    break
                time.sleep(0.15 * (attempt + 1))

        if last_exc is not None:
            raise _wrap_network_error(last_exc, current_url)

        response = Response(
            status_code=status_code,
            reason=reason,
            headers=headers,
            body=body,
            url=current_url,
            from_cache=False,
        )

        location = headers.get("location")
        if 300 <= status_code < 400 and location:
            current_url = urljoin(current_url, location)
            continue

        if cache is not None:
            cache.set(current_url, response)

        return response

    raise HTTPError(f"redirect: too many redirects while requesting {url!r}")
