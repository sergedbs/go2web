from __future__ import annotations

import base64
import hashlib
import json
import time
from pathlib import Path
from typing import Optional

from .http_client import Response


class DiskCache:
    def __init__(self, cache_dir: Optional[Path] = None, ttl_seconds: int = 600) -> None:
        self.cache_dir = cache_dir or (Path.home() / ".go2web_cache")
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.ttl_seconds = ttl_seconds

    def _path_for_url(self, url: str) -> Path:
        digest = hashlib.sha256(url.encode("utf-8")).hexdigest()
        return self.cache_dir / f"{digest}.json"

    def get(self, url: str) -> Optional[Response]:
        path = self._path_for_url(url)
        if not path.exists():
            return None

        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None

        expires_at = payload.get("expires_at", 0)
        if time.time() > expires_at:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
            return None

        try:
            body = base64.b64decode(payload["body_b64"])
            return Response(
                status_code=int(payload["status_code"]),
                reason=payload.get("reason", ""),
                headers=dict(payload.get("headers", {})),
                body=body,
                url=payload.get("url", url),
                from_cache=True,
            )
        except (KeyError, ValueError, TypeError):
            return None

    def set(self, url: str, response: Response) -> None:
        path = self._path_for_url(url)
        payload = {
            "url": response.url,
            "status_code": response.status_code,
            "reason": response.reason,
            "headers": response.headers,
            "body_b64": base64.b64encode(response.body).decode("ascii"),
            "expires_at": time.time() + self.ttl_seconds,
        }
        try:
            path.write_text(json.dumps(payload), encoding="utf-8")
        except OSError:
            # Cache failures should not break request flow.
            return

    def clear(self) -> int:
        removed = 0
        try:
            for item in self.cache_dir.glob("*.json"):
                try:
                    item.unlink()
                    removed += 1
                except OSError:
                    pass
        except OSError:
            return removed
        return removed


class NullCache:
    def get(self, url: str) -> Optional[Response]:
        return None

    def set(self, url: str, response: Response) -> None:
        return
