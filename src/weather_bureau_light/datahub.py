"""HTTP client for the Met Office Blended Probabilistic Forecast API.

Authentication is an `apikey` request header (not Bearer, not a query parameter).

The free tier is capped per day, so every response goes through a disk cache. Without
it a handful of browser reloads during development will exhaust the daily allowance.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from pathlib import Path
from typing import Any

import httpx

from .config import BASE_URL_V1, BASE_URL_V2, DEFAULT_INSTANCE, Config

log = logging.getLogger(__name__)


class DataHubError(RuntimeError):
    """An API request failed in a way the caller should surface to the user."""


class AuthError(DataHubError):
    """The key was rejected, or is not subscribed to this product."""


class DiskCache:
    def __init__(self, directory: Path) -> None:
        self.directory = directory

    def _path(self, key: str) -> Path:
        digest = hashlib.sha256(key.encode()).hexdigest()[:32]
        return self.directory / f"{digest}.json"

    def get(
        self, key: str, ttl: int, hour_aligned: bool = False, floor: int = 300
    ) -> Any | None:
        path = self._path(key)
        if not path.is_file():
            return None
        try:
            envelope = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(envelope, dict) or "stored_at" not in envelope:
            return None  # Written by an older version; treat as a miss.

        stored_at = envelope["stored_at"]
        now = time.time()
        age = now - stored_at

        if hour_aligned:
            # The forecast rolls on the hour: its time axis advances one step and the
            # leading columns drop off. Expiring on the same boundary keeps the table
            # from showing hours that have already passed, and costs at most 24
            # refetches a day rather than the 96 a 15-minute TTL would.
            crossed_hour = int(now // 3600) != int(stored_at // 3600)
            # A fetch at 09:59 would otherwise expire a minute later; the floor stops
            # that thrash without letting the data drift into the past.
            if crossed_hour and age >= floor:
                log.debug("cache crossed the hour boundary (%.0fs old): %s", age, key)
                return None
        elif age > ttl:
            log.debug("cache stale (%.0fs > %ds): %s", age, ttl, key)
            return None

        log.info("cache hit (%.0fs old): %s", age, key)
        return envelope["payload"]

    def set(self, key: str, value: Any) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self._path(key)
        # The write time is recorded in the file rather than read back from its mtime,
        # which a copy or a backup restore would silently reset.
        envelope = {"stored_at": time.time(), "payload": value}
        # Write via a temp file so a crash mid-write cannot leave corrupt JSON.
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
        ttl = self.config.cache_ttl if ttl is None else ttl
        cache_key = f"{self.base_url}{path}?{sorted((params or {}).items())}"

        cached = self.cache.get(cache_key, ttl, hour_aligned=hour_aligned)
        if cached is not None:
            return cached

        try:
            payload = self._request(path, params)
        except DataHubError:
            # Prefer stale data over an error page: a forecast a few hours old is far
            # more useful than nothing when the quota is spent or the API is down.
            stale = self.cache.get(cache_key, ttl=10**9)
            if stale is not None:
                log.warning("serving stale cache for %s", path)
                return stale
            raise

        self.cache.set(cache_key, payload)
        return payload

    def ensure_base_url(self) -> str:
        """Pick a service version this key is subscribed to.

        Two versions are live and keys are not always valid for both, so fall back
        rather than failing outright.
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
        """The instance that carries the data. Only one ("blended") exists today, but
        it is resolved rather than hardcoded so a rename does not break the app."""
        if collection_id in self._instances:
            return self._instances[collection_id]
        # The instance's temporal extent rolls with the forecast, so it expires on the
        # same boundary; otherwise this costs more calls than the forecast itself.
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
    ) -> dict[str, Any]:
        instance = self.instance(collection_id)
        params: dict[str, str] = {}
        if parameters:
            params["parameter-name"] = ",".join(parameters)
        return self.get(
            f"/collections/{collection_id}/instances/{instance}/locations/{location_id}",
            params,
            hour_aligned=True,
        )
