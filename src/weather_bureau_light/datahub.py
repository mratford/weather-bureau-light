"""HTTP client for the Met Office Blended Probabilistic Forecast API.

Authentication is an `apikey` request header (not Bearer, not a query parameter).

The free tier has a daily limit, so responses are stored in a disk cache.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from .config import BASE_URL_V1, BASE_URL_V2, DEFAULT_INSTANCE, UK_TZ, Config

log = logging.getLogger(__name__)


class DataHubError(RuntimeError):
    """Raised when an API request fails."""


class AuthError(DataHubError):
    """Raised when the key is rejected or lacks the required subscription."""


def _local(stamp: float) -> datetime:
    """Convert an epoch timestamp to an aware UK-local datetime."""
    return datetime.fromtimestamp(stamp, timezone.utc).astimezone(UK_TZ)


@dataclass(frozen=True)
class CacheEntry:
    """A cached payload and its write time."""

    payload: Any
    stored_at: float


@dataclass(frozen=True)
class Fetched:
    """A payload with its retrieval time and fallback status.

    The retrieval time is kept with the payload so the page can distinguish live data
    from a stale fallback and report when the data was obtained.
    """

    payload: Any
    retrieved_at: datetime
    stale: bool = False


class DiskCache:
    def __init__(self, directory: Path) -> None:
        self.directory = directory

    def _path(self, key: str) -> Path:
        digest = hashlib.sha256(key.encode()).hexdigest()[:32]
        return self.directory / f"{digest}.json"

    def get(
        self, key: str, ttl: int, hour_aligned: bool = False, floor: int = 300
    ) -> Any | None:
        """Return the cached payload, or None if it is unavailable."""
        entry = self.get_entry(key, ttl, hour_aligned=hour_aligned, floor=floor)
        return None if entry is None else entry.payload

    def get_entry(
        self, key: str, ttl: int, hour_aligned: bool = False, floor: int = 300
    ) -> CacheEntry | None:
        path = self._path(key)
        if not path.is_file():
            return None
        try:
            envelope = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(envelope, dict) or "stored_at" not in envelope:
            return None  # Older cache format; treat it as a miss.

        stored_at = envelope["stored_at"]
        now = time.time()
        age = now - stored_at

        if hour_aligned:
            # The forecast advances on the hour. Expire the cache at that boundary so
            # the table does not retain hours that have already passed.
            crossed_hour = int(now // 3600) != int(stored_at // 3600)
            # Keep a small minimum age so a fetch just before the boundary is not
            # immediately replaced.
            if crossed_hour and age >= floor:
                log.debug("cache crossed the hour boundary (%.0fs old): %s", age, key)
                return None
        elif age > ttl:
            log.debug("cache stale (%.0fs > %ds): %s", age, ttl, key)
            return None

        log.info("cache hit (%.0fs old): %s", age, key)
        return CacheEntry(payload=envelope["payload"], stored_at=stored_at)

    def set(self, key: str, value: Any) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self._path(key)
        # Store the write time in the file so copying or restoring it does not change it.
        envelope = {"stored_at": time.time(), "payload": value}
        # Replace the cache atomically so an interrupted write cannot leave bad JSON.
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(envelope))
        tmp.replace(path)


class DataHubClient:
    def __init__(self, config: Config, client: httpx.Client | None = None) -> None:
        self.config = config
        self.base_url = config.base_url
        self.cache = DiskCache(config.cache_dir)
        self._client = client or httpx.Client(
            headers={"apikey": config.api_key, "accept": "application/json"},
            timeout=30.0,
            follow_redirects=True,
        )
        self._base_url_checked = False
        self._instances: dict[str, str] = {}
        # These values let /healthz report API status without making another request.
        self.last_success_at: datetime | None = None
        self.last_failure_at: datetime | None = None
        self.last_failure: str | None = None

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> DataHubClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _request(self, path: str, params: dict[str, str] | None) -> Any:
        url = f"{self.base_url}{path}"
        last_error: Exception | None = None

        for attempt in range(3):
            try:
                response = self._client.get(url, params=params or None)
            except httpx.HTTPError as exc:
                last_error = exc
                log.warning("request failed (attempt %d): %s", attempt + 1, exc)
                time.sleep(2**attempt)
                continue

            if response.status_code == 200:
                return response.json()
            if response.status_code in (401, 403):
                raise AuthError(
                    f"HTTP {response.status_code} from {url}. Check METOFFICE_API_KEY is "
                    "correct and subscribed to the Blended Probabilistic Forecast product."
                )
            if response.status_code == 404:
                raise DataHubError(f"Not found: {url}")
            if response.status_code == 429:
                raise DataHubError(
                    "Rate limit reached (HTTP 429). The free tier has a daily call cap; "
                    "cached data will still render."
                )
            if 500 <= response.status_code < 600:
                last_error = DataHubError(f"HTTP {response.status_code}")
                time.sleep(2**attempt)
                continue
            raise DataHubError(f"HTTP {response.status_code} from {url}: {response.text[:200]}")

        raise DataHubError(f"Request to {url} failed after 3 attempts: {last_error}")

    def get(
        self,
        path: str,
        params: dict[str, str] | None = None,
        ttl: int | None = None,
        hour_aligned: bool = False,
    ) -> Any:
        """Return only the payload; use fetch() when its age is needed."""
        return self.fetch(path, params, ttl=ttl, hour_aligned=hour_aligned).payload

    def fetch(
        self,
        path: str,
        params: dict[str, str] | None = None,
        ttl: int | None = None,
        hour_aligned: bool = False,
    ) -> Fetched:
        ttl = self.config.cache_ttl if ttl is None else ttl
        cache_key = f"{self.base_url}{path}?{sorted((params or {}).items())}"

        cached = self.cache.get_entry(cache_key, ttl, hour_aligned=hour_aligned)
        if cached is not None:
            return Fetched(cached.payload, retrieved_at=_local(cached.stored_at))

        try:
            payload = self._request(path, params)
        except DataHubError as exc:
            self.last_failure_at = _local(time.time())
            self.last_failure = str(exc)
            # A stale forecast is still useful during an outage, provided the page
            # identifies it as stale.
            stale = self.cache.get_entry(cache_key, ttl=10**9)
            if stale is not None:
                log.warning("serving stale cache for %s", path)
                return Fetched(stale.payload, retrieved_at=_local(stale.stored_at), stale=True)
            raise

        now = time.time()
        self.last_success_at = _local(now)
        self.cache.set(cache_key, payload)
        return Fetched(payload, retrieved_at=_local(now))

    def ensure_base_url(self) -> str:
        """Choose a service version accepted by this key.

        Two versions are live, and a key may be valid for only one of them.
        """
        if self._base_url_checked:
            return self.base_url
        candidates = list(dict.fromkeys([self.config.base_url, BASE_URL_V2, BASE_URL_V1]))
        errors = []
        for base in candidates:
            self.base_url = base
            try:
                self.get("/collections", ttl=self.config.site_catalogue_ttl)
                self._base_url_checked = True
                log.info("using base URL %s", base)
                return base
            except DataHubError as exc:
                errors.append(f"{base}: {exc}")
        raise AuthError("No API version accepted this key.\n" + "\n".join(errors))

    def collections(self) -> dict[str, Any]:
        return self.get("/collections", ttl=self.config.site_catalogue_ttl)

    def collection(self, collection_id: str) -> dict[str, Any] | None:
        for entry in self.collections().get("collections", []):
            if isinstance(entry, dict) and entry.get("id") == collection_id:
                return entry
        return None

    def instance(self, collection_id: str) -> str:
        """Return the data instance, resolving its id from the collection."""
        if collection_id in self._instances:
            return self._instances[collection_id]
        # The instance changes with the forecast, so use the same hour-aligned cache.
        doc = self.get(f"/collections/{collection_id}/instances", hour_aligned=True)
        ids = [i["id"] for i in doc.get("instances", []) if isinstance(i, dict) and "id" in i]
        instance = ids[-1] if ids else DEFAULT_INSTANCE
        self._instances[collection_id] = instance
        return instance

    def locations(self, collection_id: str) -> dict[str, Any]:
        instance = self.instance(collection_id)
        return self.get(
            f"/collections/{collection_id}/instances/{instance}/locations",
            ttl=self.config.site_catalogue_ttl,
        )

    def forecast(
        self, collection_id: str, location_id: str, parameters: list[str] | None = None
    ) -> Fetched:
        """Return the forecast document together with its retrieval time."""
        instance = self.instance(collection_id)
        params: dict[str, str] = {}
        if parameters:
            params["parameter-name"] = ",".join(parameters)
        return self.fetch(
            f"/collections/{collection_id}/instances/{instance}/locations/{location_id}",
            params,
            hour_aligned=True,
        )
