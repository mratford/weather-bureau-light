"""Tests for unit conversion, banding, and solar calculations."""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from weather_bureau_light import astro, symbols, units

UK = ZoneInfo("Europe/London")
BRENTWOOD = (51.6214, 0.3053)


def test_kelvin_to_celsius():
    assert units.kelvin_to_celsius(273.15) == pytest.approx(0.0)
    assert units.kelvin_to_celsius(293.15) == pytest.approx(20.0)
    assert units.kelvin_to_celsius(None) is None


def test_ms_to_mph():
    assert units.ms_to_mph(10) == pytest.approx(22.369, abs=1e-3)
    assert units.ms_to_mph(None) is None


def test_pa_to_hpa():
    # 102680 Pa is a real value from a Met Office sample response.
    assert units.pa_to_hpa(102680) == pytest.approx(1026.8)


def test_fraction_to_percent_handles_both_conventions():
    assert units.fraction_to_percent(0.35) == pytest.approx(35.0)
    assert units.fraction_to_percent(35.0) == pytest.approx(35.0)
    assert units.fraction_to_percent(1.0) == pytest.approx(100.0)


@pytest.mark.parametrize(
    "degrees,expected",
    [(0, "N"), (90, "E"), (180, "S"), (270, "W"), (45, "NE"), (359, "N"), (22.5, "NNE")],
)
def test_compass_point(degrees, expected):
    assert units.compass_point(degrees) == expected


def test_compass_point_wraps_past_360():
    assert units.compass_point(361) == "N"


@pytest.mark.parametrize(
    "metres,code",
    [(500, "VP"), (1000, "VP"), (2000, "P"), (8000, "M"), (15000, "G"), (30000, "VG"), (50000, "E")],
)
def test_visibility_bands(metres, code):
    assert units.visibility_band(metres).code == code


@pytest.mark.parametrize(
    "index,label",
    [(1, "Low"), (2, "Low"), (3, "Moderate"), (5, "Moderate"), (6, "High"), (8, "Very high"), (11, "Extreme")],
)
def test_uv_bands(index, label):
    assert units.uv_band(index) == label


def test_symbol_lookup():
    assert symbols.lookup(1).label == "Sunny day"
    assert symbols.lookup(0).night is True
    assert symbols.lookup(30).label == "Thunder"
    assert symbols.lookup(None).sprite == "unknown"
    assert symbols.lookup(999).sprite == "unknown"


def test_symbol_lookup_accepts_float_codes():
    """Percentile ranges come back as floats even for the deterministic symbol."""
    assert symbols.lookup(7.0).label == "Cloudy"


def test_sunrise_sunset_midsummer_brentwood():
    sunrise, sunset = astro.sun_times(date(2026, 6, 21), *BRENTWOOD, UK)
    # Around 04:43 and 21:21 BST at this latitude.
    assert sunrise.hour == 4 and 35 <= sunrise.minute <= 55
    assert sunset.hour == 21 and 10 <= sunset.minute <= 30
    assert sunrise.utcoffset().total_seconds() == 3600  # BST


def test_sunrise_sunset_midwinter_brentwood():
    sunrise, sunset = astro.sun_times(date(2026, 12, 21), *BRENTWOOD, UK)
    assert sunrise.hour == 8
    assert sunset.hour == 15
    assert sunrise.utcoffset().total_seconds() == 0  # GMT


def test_daylight_flag():
    assert astro.is_daylight(datetime(2026, 8, 15, 13, 0, tzinfo=UK), *BRENTWOOD, UK)
    assert not astro.is_daylight(datetime(2026, 8, 15, 2, 0, tzinfo=UK), *BRENTWOOD, UK)


def test_polar_night_returns_none():
    sunrise, sunset = astro.sun_times(date(2026, 12, 21), 78.9, 11.9, ZoneInfo("UTC"))
    assert sunrise is None and sunset is None
