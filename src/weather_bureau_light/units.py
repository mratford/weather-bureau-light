"""Conversions from the API's SI units to the units the old Met Office page showed.

The BPF API returns Kelvin, m/s, Pa and metres. The forecast table showed degrees
Celsius, mph, hPa and visibility bands.
"""

from __future__ import annotations

from dataclasses import dataclass

KELVIN_OFFSET = 273.15
MS_TO_MPH = 2.236936
PA_TO_HPA = 0.01

# The Met Office bolds gusts at or above 25 knots on the forecast table.
STRONG_GUST_MPH = 29

COMPASS_POINTS = (
    "N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
    "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW",
)


def kelvin_to_celsius(value: float | None) -> float | None:
    return None if value is None else value - KELVIN_OFFSET


def ms_to_mph(value: float | None) -> float | None:
    return None if value is None else value * MS_TO_MPH


def pa_to_hpa(value: float | None) -> float | None:
    return None if value is None else value * PA_TO_HPA


def fraction_to_percent(value: float | None) -> float | None:
    """Probabilities come back as 0-1 in some parameters and 0-100 in others."""
    if value is None:
        return None
    return value * 100 if value <= 1.0 else value


def compass_point(degrees: float | None) -> str | None:
    """Meteorological wind direction (the direction the wind blows *from*)."""
    if degrees is None:
        return None
    index = int((degrees % 360) / 22.5 + 0.5) % 16
    return COMPASS_POINTS[index]


@dataclass(frozen=True)
class Band:
    code: str
    label: str


# Ranges as published in the Met Office forecast key.
VISIBILITY_BANDS = (
    (1_000, Band("VP", "Very poor")),
    (4_000, Band("P", "Poor")),
    (10_000, Band("M", "Moderate")),
    (20_000, Band("G", "Good")),
    (40_000, Band("VG", "Very good")),
)
VISIBILITY_EXCELLENT = Band("E", "Excellent")


def visibility_band(metres: float | None) -> Band | None:
    if metres is None:
        return None
    for upper, band in VISIBILITY_BANDS:
        if metres <= upper:
            return band
    return VISIBILITY_EXCELLENT


UV_BANDS = (
    (2, "Low"),
    (5, "Moderate"),
    (7, "High"),
    (10, "Very high"),
)


def uv_band(index: float | None) -> str | None:
    if index is None:
        return None
    for upper, label in UV_BANDS:
        if index <= upper:
            return label
    return "Extreme"


def round_or_none(value: float | None, digits: int = 0) -> float | int | None:
    if value is None:
        return None
    return int(round(value)) if digits == 0 else round(value, digits)
