import time

from go2web.cache import DiskCache, NullCache
from go2web.http_client import Response


def test_set_and_get(tmp_path):
    cache = DiskCache(cache_dir=tmp_path, ttl_seconds=60)
    response = Response(
        status_code=200,
        reason="OK",
        headers={"content-type": "text/plain"},
        body=b"hello",
        url="https://example.com",
    )
    cache.set("https://example.com", response)
    restored = cache.get("https://example.com")
    assert restored is not None
    assert restored.from_cache
    assert restored.body == b"hello"


def test_expired_entry(tmp_path):
    cache = DiskCache(cache_dir=tmp_path, ttl_seconds=0)
    response = Response(
        status_code=200,
        reason="OK",
        headers={},
        body=b"stale",
        url="https://example.com",
    )
    cache.set("https://example.com", response)
    time.sleep(0.01)
    restored = cache.get("https://example.com")
    assert restored is None


def test_clear_cache(tmp_path):
    cache = DiskCache(cache_dir=tmp_path, ttl_seconds=60)
    response = Response(200, "OK", {}, b"x", "https://example.com")
    cache.set("https://example.com/1", response)
    cache.set("https://example.com/2", response)
    removed = cache.clear()
    assert removed == 2


def test_null_cache():
    cache = NullCache()
    response = Response(200, "OK", {}, b"x", "https://example.com")
    cache.set("https://example.com", response)
    assert cache.get("https://example.com") is None
