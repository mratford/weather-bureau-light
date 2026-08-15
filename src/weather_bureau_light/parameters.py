"""Resolve the logical fields the forecast table needs onto real API parameter names.

The Met Office docs never enumerate the BPF parameter names - they tell you to read
the collection endpoint - and the names differ between the percentile and probability
collections. So rather than hardcode a guess, each field carries ranked regex patterns
and is matched against the parameter list the service actually reports. First pattern
to match wins, so the patterns are ordered most- to least-specific.

`scripts/discover.py` prints the live names; `unresolved()` reports anything that
failed to match so a mismatch surfaces loudly instead of rendering as a blank row.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable, Iterable

from . import units


@dataclass(frozen=True)
class Field:
    """One row of the forecast table."""

    key: str
    label: str
    patterns: tuple[str, ...]
    convert: Callable[[float | None], float | None] | None = None
    unit: str = ""
    #: Percentile fields get a median plus a 10th-90th range; deterministic ones do not.
    probabilistic: bool = True
    #: Coarser-resolution parameters covering the tail of the forecast. Some parameters
    #: are published hourly (Pt01h) only for the first few days and three-hourly
    #: (Pt03h) beyond that, so both are fetched and the finer one wins where they
    #: overlap. Without this, gusts and weather symbols vanish after about day five.
    fallback_patterns: tuple[str, ...] = ()


def _p(*patterns: str) -> tuple[str, ...]:
    return patterns


# Percentile collection. Patterns are anchored on the names confirmed live by
# scripts/discover.py, with looser fallbacks in case the service renames things.
#
# Several parameters exist in hourly and three-hourly forms (Pt01h / Pt03h). The
# hourly one is preferred; the three-hourly is the fallback for the later part of the
# forecast, where the API stops publishing hourly data.
PERCENTILE_FIELDS: tuple[Field, ...] = (
    Field(
        "temperature",
        "Temperature",
        # Must not match the Maximum/Minimum Pt12h aggregates.
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
        # The plain 1.5m parameter, not the "in vicinity" variant.
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
        # Not the Pt24h maximum.
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

# Probability collection: the chance-of-precipitation row. This parameter carries a
# threshold axis, and the threshold is chosen in model.py rather than here.
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
    """The outcome of matching fields against a live parameter list."""

    mapping: dict[str, str] = field(default_factory=dict)
    missing: list[str] = field(default_factory=list)
    #: Coarser-resolution sources per field, tried after the primary one.
    fallbacks: dict[str, list[str]] = field(default_factory=dict)

    def name_for(self, key: str) -> str | None:
        return self.mapping.get(key)

    def names_for(self, key: str) -> list[str]:
        """Every source for a field, finest resolution first."""
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
    """Compare case- and separator-insensitively.

    The API mixes conventions - camelCase in CoverageJSON (`airTemperature`) against
    snake_case in the underlying CF names (`air_temperature`) - so both collapse to a
    single underscore-separated lowercase form before matching.
    """
    spaced = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", name)
    return re.sub(r"[^a-z0-9]+", "_", spaced.lower()).strip("_")


def resolve(available: Iterable[str], fields: Iterable[Field]) -> Resolution:
    """Match each field against the available parameter names."""
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
                # Shortest name wins: the plainest parameter rather than a variant.
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
            # A field whose only source is the coarser one is still usable.
            if chosen is None:
                result.missing.remove(spec.key)

    return result


def field_by_key(key: str) -> Field | None:
    for spec in (*PERCENTILE_FIELDS, *PROBABILITY_FIELDS):
        if spec.key == key:
            return spec
    return None
