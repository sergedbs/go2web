import socket
from unittest.mock import patch

import pytest

from go2web.http_client import HTTPError, _decode_chunked, _parse_url, fetch


class FakeSocket:
    def __init__(self, chunks):
        self._chunks = list(chunks)

    def recv(self, _size):
        if not self._chunks:
            return b""
        return self._chunks.pop(0)


class FakeConnection:
    def __init__(self, payload: bytes):
        self._payload = payload
        self._sent = False

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def sendall(self, _data):
        self._sent = True

    def recv(self, _size):
        if not self._sent:
            return b""
        if self._payload:
            data = self._payload
            self._payload = b""
            return data
        return b""


def test_parse_url_defaults():
    scheme, host, port, path = _parse_url("https://example.com/a?q=1")
    assert (scheme, host, port, path) == ("https", "example.com", 443, "/a?q=1")


def test_parse_url_rejects_scheme():
    with pytest.raises(HTTPError):
        _parse_url("ftp://example.com")


def test_decode_chunked():
    initial = b"4\r\nWiki\r\n"
    sock = FakeSocket([b"5\r\npedia\r\n0\r\n\r\n"])
    decoded = _decode_chunked(sock, initial)
    assert decoded == b"Wikipedia"


def test_fetch_retries_on_timeout():
    payload = b"HTTP/1.1 200 OK\r\nContent-Length: 5\r\n\r\nhello"
    with patch(
        "go2web.http_client._open_socket",
        side_effect=[socket.timeout("boom"), FakeConnection(payload)],
    ):
        response = fetch("http://example.com", retries=1)
    assert response.status_code == 200
    assert response.body == b"hello"


def test_fetch_timeout_error_message():
    with patch("go2web.http_client._open_socket", side_effect=socket.timeout("boom")):
        with pytest.raises(HTTPError) as ctx:
            fetch("http://example.com", retries=0)
    assert "timeout:" in str(ctx.value)
