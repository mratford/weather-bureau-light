"""Sunrise and sunset times.

The old design's day tabs showed these, but the BPF API does not supply them, so they
are computed from the NOAA solar position equations. Accurate to well under a minute
at UK latitudes, which is all the display needs.
"""

from __future__ import annotations

import math
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

# Solar zenith at sunrise/sunset, including the standard refraction allowance.
_ZENITH = math.radians(90.833)


def _julian_day(day: date) -> float:
    y, m, d = day.year, day.month, day.day
    if m <= 2:
        y, m = y - 1, m + 12
    a = y // 100
    b = 2 - a + a // 4
    return int(365.25 * (y + 4716)) + int(30.6001 * (m + 1)) + d + b - 1524.5


def _solar_events(day: date, latitude: float, longitude: float) -> tuple[float, float] | None:
    """Return (sunrise, sunset) as UTC hours, or None for polar day/night."""
    julian_century = (_julian_day(day) - 2451545.0) / 36525.0

    geom_mean_long = math.radians(
        (280.46646 + julian_century * (36000.76983 + julian_century * 0.0003032)) % 360
    )
    geom_mean_anom = math.radians(
        357.52911 + julian_century * (35999.05029 - 0.0001537 * julian_century)
    )
    eccentricity = 0.016708634 - julian_century * (
        0.000042037 + 0.0000001267 * julian_century
    )

    centre = (
        math.sin(geom_mean_anom)
        * (1.914602 - julian_century * (0.004817 + 0.000014 * julian_century))
        + math.sin(2 * geom_mean_anom) * (0.019993 - 0.000101 * julian_century)
        + math.sin(3 * geom_mean_anom) * 0.000289
    )
    true_long = math.degrees(geom_mean_long) + centre
    apparent_long = math.radians(
        true_long
        - 0.00569
        - 0.00478 * math.sin(math.radians(125.04 - 1934.136 * julian_century))
    )

    mean_obliquity = (
        23
        + (26 + ((21.448 - julian_century * (46.815 + julian_century * (0.00059 - julian_century * 0.001813)))) / 60)
        / 60
    )
    obliquity = math.radians(
        mean_obliquity + 0.00256 * math.cos(math.radians(125.04 - 1934.136 * julian_century))
    )

    declination = math.asin(math.sin(obliquity) * math.sin(apparent_long))

    var_y = math.tan(obliquity / 2) ** 2
    equation_of_time = 4 * math.degrees(
        var_y * math.sin(2 * geom_mean_long)
        - 2 * eccentricity * math.sin(geom_mean_anom)
        + 4 * eccentricity * var_y * math.sin(geom_mean_anom) * math.cos(2 * geom_mean_long)
        - 0.5 * var_y * var_y * math.sin(4 * geom_mean_long)
        - 1.25 * eccentricity * eccentricity * math.sin(2 * geom_mean_anom)
    )

    lat = math.radians(latitude)
    cos_hour_angle = (math.cos(_ZENITH) / (math.cos(lat) * math.cos(declination))) - (
        math.tan(lat) * math.tan(declination)
    )
    if not -1.0 <= cos_hour_angle <= 1.0:
        return None  # Sun never rises or never sets on this day.

    hour_angle = math.degrees(math.acos(cos_hour_angle))
    solar_noon = (720 - 4 * longitude - equation_of_time) / 60
    return solar_noon - hour_angle / 15, solar_noon + hour_angle / 15


def _to_local(day: date, utc_hours: float, tz: ZoneInfo) -> datetime:
    base = datetime.combine(day, time(0, 0), tzinfo=timezone.utc)
    return (base + timedelta(hours=utc_hours)).astimezone(tz)


def sun_times(
    day: date, latitude: float, longitude: float, tz: ZoneInfo
) -> tuple[datetime | None, datetime | None]:
    """Local sunrise and sunset for a date, or (None, None) inside a polar day/night."""
    events = _solar_events(day, latitude, longitude)
    if events is None:
        return None, None
    rise_utc, set_utc = events
    return _to_local(day, rise_utc, tz), _to_local(day, set_utc, tz)


def is_daylight(moment: datetime, latitude: float, longitude: float, tz: ZoneInfo) -> bool:
    """Whether a given instant falls between sunrise and sunset.

    Drives the day/night variant of the weather symbol, since the API's weather code
    already distinguishes them but a fallback is needed when it does not.
    """
    local = moment.astimezone(tz)
    sunrise, sunset = sun_times(local.date(), latitude, longitude, tz)
    if sunrise is None or sunset is None:
        return True
    return sunrise <= local <= sunset
