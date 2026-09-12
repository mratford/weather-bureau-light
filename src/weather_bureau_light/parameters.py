"""Map the forecast table's logical fields to API parameter names.

The API uses different names in the percentile and probability collections, and the
documentation does not list them. Each field therefore has ordered regular-expression
patterns that are matched against the names returned by the service.

`scripts/discover.py` prints the live names, and `unresolved()` reports fields that do
not match so they are not silently rendered as blank rows.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable, Iterable

from . import units


@dataclass(frozen=True)
class Field:
    """One forecast table row."""

    key: str
    label: str
    patterns: tuple[str, ...]
    convert: Callable[[float | None], float | None] | None = None
    unit: str = ""
    #: Percentile fields have a median and 10th-90th range; deterministic fields do not.
    probabilistic: bool = True
    #: Coarser-resolution parameters covering the end of the forecast. Some fields are
    #: hourly (Pt01h) for the first few days and three-hourly (Pt03h) later, so both
    #: are fetched and the finer source takes priority where they overlap.
    fallback_patterns: tuple[str, ...] = ()


def _p(*patterns: str) -> tuple[str, ...]:
    return patterns


# Percentile collection. Patterns use names confirmed by scripts/discover.py, with
# less specific fallbacks in case the service changes them.
#
# Several parameters have hourly and three-hourly forms (Pt01h / Pt03h). Prefer the
# hourly form and use the three-hourly form later, when hourly data is unavailable.
PERCENTILE_FIELDS: tuple[Field, ...] = (
    Field(
        "temperature",
        "Temperature",
        # Exclude the Maximum/Minimum Pt12h aggregates.
        _p(r"^air_?temperature1p5m$", r"^air_?temperature$", r"screen.*temperature"),
        units.kelvin_to_celsius,
        "°C",
    ),
    Field(
        "feels_like",
        "Feels like",
        _p(r"^feels_?like_?temperature1p5m$", r"feels_?like.*temperature", r"apparent.*temperature"),
        units.kelvin_to_celsius,
        "°C",
    ),
    Field(
        "wind_speed",
        "Wind speed",
        _p(r"^wind_?speed10m$", r"^wind_?speed(_?at)?_?10m$", r"^wind_?speed$"),
        units.ms_to_mph,
        "mph",
    ),
    Field(
        "wind_gust",
        "Wind gust",
        _p(
            r"^wind_?speed_?of_?gust10m_?maximum_?pt01h$",
            r"wind_?speed_?of_?gust.*10m.*pt01h",
            r"wind_?speed_?of_?gust.*10m",
            r"gust",
        ),
        units.ms_to_mph,
        "mph",
        fallback_patterns=_p(r"^wind_?speed_?of_?gust10m_?maximum_?pt03h$"),
    ),
    Field(
        "wind_direction",
        "Wind direction",
        _p(r"^wind_?from_?direction10m_?mean$", r"wind_?from_?direction.*10m", r"wind_?.*direction"),
        None,
        "°",
    ),
    Field(
        "humidity",
        "Humidity",
        _p(r"^relative_?humidity1p5m$", r"relative_?humidity"),
        units.fraction_to_percent,
        "%",
    ),
    Field(
        "visibility",
        "Visibility",
        # Use the plain 1.5m parameter, not the "in vicinity" variant.
        _p(r"^visibility_?in_?air1p5m$", r"^visibility.*1p5m$", r"^visibility"),
        None,
        "m",
    ),
    Field(
        "pressure",
        "Pressure",
        _p(r"^air_?pressure_?at_?sea_?level$", r"pressure.*sea_?level", r"^mslp$"),
        units.pa_to_hpa,
        "hPa",
    ),
    Field(
        "uv",
        "UV index",
        # Exclude the Pt24h maximum.
        _p(r"^ultraviolet_?index$", r"^uv_?index$", r"ultraviolet.*index"),
        None,
        "",
    ),
    Field(
        "weather_code",
        "Weather",
        _p(r"^weather_?code_?pt01h$", r"^weather_?code$", r"weather_?symbol"),
        None,
        "",
        probabilistic=False,
        fallback_patterns=_p(r"^weather_?code_?pt03h$"),
    ),
)

# Probability collection: the chance-of-precipitation row. It has a threshold axis,
# and model.py selects the threshold.
PROBABILITY_FIELDS: tuple[Field, ...] = (
    Field(
        "precipitation_probability",
        "Chance of precipitation",
        _p(
            r"^probability_?of_?lwe_?precipitation_?rate_?above_?threshold$",
            r"^probability_?of_?rainfall_?rate_?above_?threshold$",
            r"probability.*precipitation_?rate.*above.*threshold",
            r"probability.*precipitation.*above",
        ),
        units.fraction_to_percent,
        "%",
        probabilistic=False,
    ),
)


@dataclass
class Resolution:
    """The result of matching fields against a live parameter list."""

    mapping: dict[str, str] = field(default_factory=dict)
    missing: list[str] = field(default_factory=list)
    #: Coarser-resolution sources for each field, tried after the primary source.
    fallbacks: dict[str, list[str]] = field(default_factory=dict)

    def name_for(self, key: str) -> str | None:
        return self.mapping.get(key)

    def names_for(self, key: str) -> list[str]:
        """Return every source for a field, finest resolution first."""
        primary = self.mapping.get(key)
        names = [primary] if primary else []
        return names + [n for n in self.fallbacks.get(key, []) if n != primary]

    def all_names(self) -> list[str]:
        seen: list[str] = []
        for key in list(self.mapping) + list(self.fallbacks):
            for name in self.names_for(key):
                if name not in seen:
                    seen.append(name)
        return seen


def _normalise(name: str) -> str:
    """Compare names without regard to case or separators.

    The API mixes camelCase in CoverageJSON with snake_case in CF names, so both forms
    are reduced to lowercase words separated by underscores before matching.
    """
    spaced = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", name)
    return re.sub(r"[^a-z0-9]+", "_", spaced.lower()).strip("_")


def resolve(available: Iterable[str], fields: Iterable[Field]) -> Resolution:
    """Resolve each field against the available parameter names."""
    names = list(available)
    normalised = {name: _normalise(name) for name in names}
    result = Resolution()

    claimed: set[str] = set()

    def match(patterns: tuple[str, ...]) -> str | None:
        for pattern in patterns:
            candidates = [
                name
                for name, norm in normalised.items()
                if re.search(pattern, norm) and name not in claimed
            ]
            if candidates:
                # Prefer the shortest, least specific matching name.
                return min(candidates, key=lambda n: (len(n), n))
        return None

    for spec in fields:
        chosen = match(spec.patterns)
        if chosen:
            result.mapping[spec.key] = chosen
            claimed.add(chosen)
        else:
            result.missing.append(spec.key)

        fallback = match(spec.fallback_patterns) if spec.fallback_patterns else None
        if fallback:
            result.fallbacks.setdefault(spec.key, []).append(fallback)
            claimed.add(fallback)
            # A field with only a coarser source is still usable.
            if chosen is None:
                result.missing.remove(spec.key)

    return result


def field_by_key(key: str) -> Field | None:
    for spec in (*PERCENTILE_FIELDS, *PROBABILITY_FIELDS):
        if spec.key == key:
            return spec
    return None
