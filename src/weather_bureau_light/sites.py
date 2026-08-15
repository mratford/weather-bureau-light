"""The spot-site catalogue: roughly 7,200 UK, Irish and Western European locations.

The BPF API addresses forecasts by site id, not by free coordinates, so the catalogue
has to be fetched, cached and searched locally to offer a location box.
"""

from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import dataclass, replace
from typing import Any, Iterable

from .datahub import DataHubClient


@dataclass(frozen=True)
class Site:
    """A BPF spot site.

    The API publishes only an id and coordinates, so `name` is usually filled in later
    by reverse geocoding rather than coming from the forecast service.
    """

    id: str
    latitude: float
    longitude: float
    name: str | None = None
    region: str | None = None
    elevation: float | None = None

    @property
    def display_name(self) -> str:
        if not self.name:
            return f"Site {self.id}"
        return f"{self.name} ({self.region})" if self.region else self.name

    def named(self, name: str | None, region: str | None = None) -> Site:
        return replace(self, name=name or self.name, region=region or self.region)


def _fold(text: str) -> str:
    """Casefold and strip accents so 'Ynys Mon' matches 'Ynys Môn'."""
    decomposed = unicodedata.normalize("NFKD", text)
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9 ]+", " ", stripped.casefold()).strip()


def parse_locations(payload: dict[str, Any]) -> list[Site]:
    """Read the GeoJSON FeatureCollection the /locations endpoint returns."""
    sites: list[Site] = []
    for feature in payload.get("features", []):
        if not isinstance(feature, dict):
            continue
        props = feature.get("properties") or {}
        coords = (feature.get("geometry") or {}).get("coordinates") or []
        if len(coords) < 2:
            continue

        site_id = feature.get("id") or props.get("id") or props.get("locationId")
        if site_id is None:
            continue

        # Properties are empty on the live service, but honour a name if one appears.
        name = props.get("name") or props.get("locationName") or props.get("title")
        sites.append(
            Site(
                id=str(site_id),
                name=str(name) if name else None,
                longitude=float(coords[0]),
                latitude=float(coords[1]),
                region=props.get("region") or props.get("county") or props.get("area"),
                elevation=float(coords[2]) if len(coords) > 2 else None,
            )
        )
    return sites


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    radius = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2
    )
    return 2 * radius * math.asin(math.sqrt(a))


class SiteCatalogue:
    def __init__(self, sites: Iterable[Site]) -> None:
        self.sites = list(sites)
        self._by_id = {site.id: site for site in self.sites}
        self._folded = [(site, _fold(site.name)) for site in self.sites if site.name]

    def __len__(self) -> int:
        return len(self.sites)

    @classmethod
    def load(cls, client: DataHubClient, collection_id: str) -> SiteCatalogue:
        return cls(parse_locations(client.locations(collection_id)))

    def get(self, site_id: str) -> Site | None:
        return self._by_id.get(site_id)

    def search(self, query: str, limit: int = 20) -> list[Site]:
        """Rank by match quality: exact, then prefix, then substring."""
        needle = _fold(query)
        if not needle:
            return []

        scored: list[tuple[int, int, Site]] = []
        for site, folded in self._folded:
            if folded == needle:
                rank = 0
            elif folded.startswith(needle):
                rank = 1
            elif re.search(rf"\b{re.escape(needle)}", folded):
                rank = 2  # matches at a word boundary inside the name
            elif needle in folded:
                rank = 3
            else:
                continue
            scored.append((rank, len(site.name), site))

        scored.sort(key=lambda row: (row[0], row[1], row[2].name))
        return [site for _, _, site in scored[:limit]]

    def nearest(self, latitude: float, longitude: float) -> Site | None:
        if not self.sites:
            return None
        return min(
            self.sites, key=lambda s: haversine_km(latitude, longitude, s.latitude, s.longitude)
        )
