"""Client and small geometry helpers for the Met Office NSWWS API."""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import httpx

from .config import UK_TZ, Config
from .datahub import DiskCache

ATOM = "{http://www.w3.org/2005/Atom}"


class WarningsError(RuntimeError):
    """The warnings service could not provide a valid response."""


@dataclass(frozen=True)
class Warning:
    """A live warning whose polygon contains the selected forecast site."""

    weather_types: tuple[str, ...]
    level: str
    headline: str
    valid_from: datetime | None
    valid_to: datetime | None
    further_details: str = ""
    what_to_expect: tuple[str, ...] = ()
    what_should_i_do: str = ""

    @property
    def weather_label(self) -> str:
        return " and ".join(t.replace("_", " ").title() for t in self.weather_types)


def _parse_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UK_TZ)
    except ValueError:
        return None


def _point_on_segment(lon: float, lat: float, a: list[float], b: list[float]) -> bool:
    cross = (lon - a[0]) * (b[1] - a[1]) - (lat - a[1]) * (b[0] - a[0])
    if abs(cross) > 1e-10:
        return False
    return min(a[0], b[0]) - 1e-10 <= lon <= max(a[0], b[0]) + 1e-10 and min(
        a[1], b[1]
    ) - 1e-10 <= lat <= max(a[1], b[1]) + 1e-10


def _point_in_ring(lon: float, lat: float, ring: Any) -> bool:
    """Return whether a longitude/latitude point is in a GeoJSON linear ring."""
    if not isinstance(ring, list) or len(ring) < 3:
        return False
    points = [p for p in ring if isinstance(p, list) and len(p) >= 2]
    if len(points) < 3:
        return False
    inside = False
    for a, b in zip(points, points[1:] + points[:1]):
        if _point_on_segment(lon, lat, a, b):
            return True
        if (a[1] > lat) != (b[1] > lat):
            crossing = (b[0] - a[0]) * (lat - a[1]) / (b[1] - a[1]) + a[0]
            if lon < crossing:
                inside = not inside
    return inside


def _point_in_geometry(lon: float, lat: float, geometry: Any) -> bool:
    """Handle the MultiPolygon geometry used by every NSWWS warning."""
    if not isinstance(geometry, dict) or geometry.get("type") != "MultiPolygon":
        return False
    coordinates = geometry.get("coordinates")
    if not isinstance(coordinates, list):
        return False
    for polygon in coordinates:
        if (
            not isinstance(polygon, list)
            or not polygon
            or not _point_in_ring(lon, lat, polygon[0])
        ):
            continue
        if all(not _point_in_ring(lon, lat, hole) for hole in polygon[1:]):
            return True
    return False


def _as_strings(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        return (value,)
    if isinstance(value, list):
        return tuple(str(item) for item in value if isinstance(item, str))
    return ()


def _parse_warning(feature: Any) -> Warning | None:
    if not isinstance(feature, dict) or not isinstance(feature.get("properties"), dict):
        return None
    props = feature["properties"]
    if props.get("warningStatus", "ISSUED") != "ISSUED":
        return None
    headline = props.get("warningHeadline")
    if not isinstance(headline, str) or not headline.strip():
        return None
    return Warning(
        weather_types=_as_strings(props.get("weatherType")),
        level=str(props.get("warningLevel", "")).upper(),
        headline=headline,
        valid_from=_parse_datetime(props.get("validFromDate")),
        valid_to=_parse_datetime(props.get("validToDate")),
        further_details=str(props.get("warningFurtherDetails", "")),
        what_to_expect=_as_strings(props.get("whatToExpect")),
        what_should_i_do=str(props.get("whatShouldIDo", "")),
    )


class WarningsClient:
    """Fetch and cache the current NSWWS snapshot, then select warnings at a point."""

    def __init__(self, config: Config, client: httpx.Client | None = None) -> None:
        self.config = config
        self.base_url = config.nswws_base_url
        self.cache = DiskCache(config.cache_dir)
        self._client = client or httpx.Client(
            headers={
                "apikey": config.nswws_api_key,
                "accept": "application/xml, application/json",
            },
            timeout=30.0,
            follow_redirects=True,
        )

    def close(self) -> None:
        self._client.close()

    def _get(self, url: str, accept: str) -> str:
        key = f"nswws:{url}"
        cached = self.cache.get_entry(key, self.config.warnings_cache_ttl)
        if cached is not None:
            return str(cached.payload)
        try:
            response = self._client.get(url, headers={"accept": accept})
        except httpx.HTTPError as exc:
            raise WarningsError(f"NSWWS request failed: {exc}") from exc
        if response.status_code in (401, 403):
            raise WarningsError(f"NSWWS authentication failed (HTTP {response.status_code})")
        if response.status_code == 429:
            raise WarningsError("NSWWS rate limit reached (HTTP 429)")
        if response.status_code != 200:
            raise WarningsError(f"NSWWS returned HTTP {response.status_code}")
        payload = response.text
        self.cache.set(key, payload)
        return payload

    def _issued_url(self) -> str | None:
        xml = self._get(
            f"{self.base_url}/objects/feed", "application/atom+xml, application/xml"
        )
        try:
            root = ET.fromstring(xml)
        except ET.ParseError as exc:
            raise WarningsError("NSWWS returned invalid Atom XML") from exc
        for link in root.findall(f"{ATOM}link"):
            if link.attrib.get("rel") == "related":
                href = link.attrib.get("href")
                if href:
                    return href
        return None

    def for_site(self, latitude: float, longitude: float) -> list[Warning]:
        if not self.config.nswws_api_key:
            return []
        try:
            issued_url = self._issued_url()
            if not issued_url:
                return []
            payload = self._get(issued_url, "application/geo+json, application/json")
            document = json.loads(payload)
            features = document.get("features", []) if isinstance(document, dict) else []
            matches = []
            for feature in features:
                warning = _parse_warning(feature)
                if warning and _point_in_geometry(longitude, latitude, feature.get("geometry")):
                    matches.append(warning)
            return matches
        except WarningsError:
            raise
        except (ValueError, TypeError, KeyError) as exc:
            raise WarningsError("NSWWS returned invalid warning data") from exc
