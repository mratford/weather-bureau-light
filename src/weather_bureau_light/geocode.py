"""Convert a place name or postcode to coordinates.

The BPF location list provides ids and coordinates but no names. postcodes.io supplies
the missing place lookup without requiring an API key.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

import httpx

from .config import POSTCODES_IO
from .datahub import DiskCache

log = logging.getLogger(__name__)

# A loose UK postcode shape; postcodes.io performs the actual validation.
POSTCODE_RE = re.compile(r"^[A-Z]{1,2}\d[A-Z\d]?\s*\d[A-Z]{2}$", re.I)


@dataclass(frozen=True)
class Place:
    name: str
    latitude: float
    longitude: float
    region: str | None = None
    kind: str = "place"

    @property
    def display_name(self) -> str:
        return f"{self.name} ({self.region})" if self.region else self.name


class Geocoder:
    def __init__(
        self,
        cache: DiskCache,
        client: httpx.Client | None = None,
        base_url: str = POSTCODES_IO,
        ttl: int = 30 * 24 * 3600,
    ) -> None:
        self.cache = cache
        self.base_url = base_url.rstrip("/")
        self.ttl = ttl
        self._client = client or httpx.Client(timeout=15.0, follow_redirects=True)

    def close(self) -> None:
        self._client.close()

    def _get(self, path: str, params: dict[str, str]) -> dict | None:
        key = f"geocode:{self.base_url}{path}?{sorted(params.items())}"
        cached = self.cache.get(key, self.ttl)
        if cached is not None:
            return cached
        try:
            response = self._client.get(f"{self.base_url}{path}", params=params)
        except httpx.HTTPError as exc:
            log.warning("geocoder unreachable: %s", exc)
            return None
        if response.status_code == 404:
            self.cache.set(key, {"result": None})
            return {"result": None}
        if response.status_code != 200:
            log.warning("geocoder returned HTTP %s", response.status_code)
            return None
        payload = response.json()
        self.cache.set(key, payload)
        return payload

    def _postcode(self, query: str) -> list[Place]:
        payload = self._get(f"/postcodes/{query.replace(' ', '').upper()}", {})
        result = (payload or {}).get("result")
        if not result:
            return []
        return [
            Place(
                name=result.get("postcode", query),
                latitude=result["latitude"],
                longitude=result["longitude"],
                region=result.get("admin_district") or result.get("region"),
                kind="postcode",
            )
        ]

    def _places(self, query: str, limit: int) -> list[Place]:
        payload = self._get("/places", {"q": query, "limit": str(limit)})
        results = (payload or {}).get("result") or []
        places = []
        for row in results:
            lat, lon = row.get("latitude"), row.get("longitude")
            if lat is None or lon is None:
                continue
            places.append(
                Place(
                    name=row.get("name_1") or row.get("name") or query,
                    latitude=lat,
                    longitude=lon,
                    region=row.get("county_unitary") or row.get("region"),
                    kind=row.get("local_type", "place"),
                )
            )
        return places

    def reverse(self, latitude: float, longitude: float) -> Place | None:
        """Return a name for a set of coordinates.

        BPF sites carry no name, so the page uses the nearest postcode district.
        """
        payload = self._get(
            "/postcodes",
            {"lat": f"{latitude:.4f}", "lon": f"{longitude:.4f}", "limit": "1", "radius": "20000"},
        )
        results = (payload or {}).get("result") or []
        if not results:
            return None
        row = results[0]
        # Use the town-level district name rather than a ward or postcode.
        name = row.get("admin_district") or row.get("admin_ward") or row.get("postcode")
        region = row.get("region")
        return Place(
            name=name,
            latitude=row.get("latitude", latitude),
            longitude=row.get("longitude", longitude),
            region=region,
            kind="reverse",
        )

    def search(self, query: str, limit: int = 10) -> list[Place]:
        query = query.strip()
        if not query:
            return []
        if POSTCODE_RE.match(query):
            found = self._postcode(query)
            if found:
                return found
        return self._places(query, limit)
