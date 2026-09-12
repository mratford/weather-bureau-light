"""Assemble parsed coverages for the forecast template.

The percentile collection supplies temperature, wind, humidity, visibility, UV,
pressure, and the weather symbol. The probability collection supplies the chance of
precipitation. The collections are merged by timestamp because their time axes may
differ.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from zoneinfo import ZoneInfo

from . import astro, covjson, symbols, units
from .covjson import Coverage, CoverageSet
from .parameters import PERCENTILE_FIELDS, PROBABILITY_FIELDS, Field, Resolution
from .sites import Site

MEDIAN = 50.0
LOWER = 10.0
UPPER = 90.0

#: Number of days shown in the day strip. Later API entries may have no weather symbol
#: and a wide uncertainty range.
MAX_DAYS = 7

#: 0.1 mm/hr in m/s, the Met Office threshold for "any precipitation".
PRECIP_THRESHOLD_MS = 0.0001 / 3600


@dataclass
class Value:
    """One forecast cell, with an optional 10th-90th percentile range."""

    median: float | None = None
    lower: float | None = None
    upper: float | None = None

    @property
    def has_range(self) -> bool:
        return (
            self.lower is not None
            and self.upper is not None
            and round(self.lower) != round(self.upper)
        )

    def rounded(self) -> int | None:
        return None if self.median is None else int(round(self.median))

    def range_text(self, unit: str = "") -> str:
        if not self.has_range:
            return "—"
        return f"{int(round(self.lower))} to {int(round(self.upper))}{unit}"


@dataclass
class Timestep:
    """One column in the forecast table."""

    time: datetime
    values: dict[str, Value] = field(default_factory=dict)
    is_daylight: bool = True

    def value(self, key: str) -> Value:
        return self.values.get(key, Value())

    def median(self, key: str) -> float | None:
        return self.value(key).median

    def rounded(self, key: str) -> int | None:
        return self.value(key).rounded()

    @property
    def label(self) -> str:
        return self.time.strftime("%H:%M")

    @property
    def symbol(self) -> symbols.Symbol:
        code = self.median("weather_code")
        resolved = symbols.lookup(code)
        if resolved is symbols.UNKNOWN:
            return resolved
        # Use the daylight calculation when a day/night code disagrees with local time.
        if resolved.night and self.is_daylight:
            for candidate in symbols._SYMBOLS.values():
                if candidate.sprite == resolved.sprite and not candidate.night:
                    return candidate
        return resolved

    @property
    def wind_direction_text(self) -> str | None:
        return units.compass_point(self.median("wind_direction"))

    @property
    def visibility_band(self) -> units.Band | None:
        return units.visibility_band(self.median("visibility"))

    @property
    def uv_band(self) -> str | None:
        return units.uv_band(self.median("uv"))

    @property
    def gust_is_strong(self) -> bool:
        gust = self.median("wind_gust")
        return gust is not None and gust >= units.STRONG_GUST_MPH


@dataclass
class Day:
    """One day tab in the day strip."""

    date: date
    timesteps: list[Timestep] = field(default_factory=list)
    sunrise: datetime | None = None
    sunset: datetime | None = None
    #: Every timestep published for this day, including those omitted from the table.
    #: The day's high and low are calculated from this complete list.
    all_timesteps: list[Timestep] = field(default_factory=list)

    def _temperatures(self) -> list[float]:
        steps = self.all_timesteps or self.timesteps
        return [v for v in (t.median("temperature") for t in steps) if v is not None]

    @property
    def max_temp(self) -> int | None:
        values = self._temperatures()
        return int(round(max(values))) if values else None

    @property
    def min_temp(self) -> int | None:
        values = self._temperatures()
        return int(round(min(values))) if values else None

    @property
    def symbol(self) -> symbols.Symbol:
        """Return the symbol forecast near the middle of the day.

        Use every timestep for the day, not only those still visible in the table.
        """
        steps = [
            t
            for t in (self.all_timesteps or self.timesteps)
            if t.median("weather_code") is not None
        ]
        daytime = [t for t in steps if t.is_daylight] or steps
        if not daytime:
            return symbols.UNKNOWN
        midday = min(daytime, key=lambda t: abs(t.time.hour - 13))
        return midday.symbol

    @property
    def iso(self) -> str:
        return self.date.isoformat()

    @property
    def tab_label(self) -> str:
        return self.date.strftime("%a %-d %b")


@dataclass
class Forecast:
    site: Site
    days: list[Day]
    issued: datetime | None = None
    """When the data was retrieved from the API."""
    stale: bool = False
    """Whether the data came from the cache after a failed live fetch."""
    missing_fields: list[str] = field(default_factory=list)

    def day(self, iso: str | None) -> Day | None:
        if not self.days:
            return None
        if iso is None:
            return self.days[0]
        return next((d for d in self.days if d.iso == iso), self.days[0])

    @property
    def age_text(self) -> str:
        """Return the rounded age used by the staleness notice."""
        if self.issued is None:
            return "an unknown time ago"
        seconds = (datetime.now(self.issued.tzinfo) - self.issued).total_seconds()
        if seconds < 90:
            return "just now"
        # Change units at their boundaries. Use days only after 36 hours.
        if seconds < 3600:
            count, unit = round(seconds / 60), "minute"
        elif seconds < 129600:
            count, unit = round(seconds / 3600), "hour"
        else:
            count, unit = round(seconds / 86400), "day"
        return f"{count} {unit}{'s' if count != 1 else ''} ago"


def _extract(
    coverages: CoverageSet, resolution: Resolution, specs, into: dict[str, dict[datetime, Value]]
) -> None:
    """Read each resolved field from its coverage and index it by timestamp.

    Parameters may use different time axes, especially in the three-hourly part of
    the forecast. Each field therefore keeps its own timestamp map, and the columns
    are reconciled later in `build`.
    """
    for spec in specs:
        # Read the finest resolution first; coarser sources fill only missing times.
        for name in resolution.names_for(spec.key):
            coverage = coverages.get(name)
            if coverage is not None:
                _extract_one(coverage, name, spec, into)


def _extract_one(
    coverage: Coverage, name: str, spec: Field, into: dict[str, dict[datetime, Value]]
) -> None:
    """Read one parameter into a field's timestamp map."""
    percentile_axis = coverage.percentile_axis() if spec.probabilistic else None
    fixed: dict[str, int] = {}

    # Probability parameters use thresholds. The chance of precipitation means
    # "any precipitation", represented by the 0.1 mm/hr threshold in m/s.
    threshold_axis = coverage.threshold_axis()
    if threshold_axis is not None:
        axis_name, thresholds = threshold_axis
        fixed[axis_name] = covjson.nearest_threshold_index(thresholds, PRECIP_THRESHOLD_MS)

    def read(target: float) -> list[float | None] | None:
        if percentile_axis is None:
            return coverage.series(name, **fixed)
        axis_name, values = percentile_axis
        index = covjson.nearest_percentile_index(values, target)
        return coverage.series(name, **{axis_name: index}, **fixed)

    median_series = read(MEDIAN)
    if median_series is None:
        return
    lower_series = read(LOWER) if percentile_axis else None
    upper_series = read(UPPER) if percentile_axis else None

    convert = spec.convert or (lambda v: v)
    series = into.setdefault(spec.key, {})

    for i, moment in enumerate(coverage.times):
        if i >= len(median_series):
            break
        # Keep a value already supplied at finer resolution.
        existing = series.get(moment)
        if existing is not None and existing.median is not None:
            continue
        series[moment] = Value(
            median=convert(median_series[i]),
            lower=convert(lower_series[i]) if lower_series and i < len(lower_series) else None,
            upper=convert(upper_series[i]) if upper_series and i < len(upper_series) else None,
        )


#: Fields ranked by how well they define the table's time grid. Temperature provides
#: the preferred column timestamps.
_ANCHOR_PREFERENCE = ("temperature", "feels_like", "wind_speed", "humidity", "pressure")


def _anchor_grid(by_field: dict[str, dict[datetime, Value]]) -> list[datetime]:
    """Choose the timestamps used as table columns.

    Parameters may use offset three-hourly grids. Use one field's axis as the grid
    and match the other fields to it instead of creating mostly empty columns.
    """
    for key in _ANCHOR_PREFERENCE:
        stamps = [m for m, v in by_field.get(key, {}).items() if v.median is not None]
        if stamps:
            return sorted(stamps)
        # If no preferred field resolved, use the field with the most timestamps.
    richest = max(by_field.values(), key=len, default={})
    return sorted(m for m, v in richest.items() if v.median is not None)


def _column(
    by_field: dict[str, dict[datetime, Value]], moment: datetime, grid: list[datetime]
) -> dict[str, Value]:
    """Assemble a table column by matching fields to the nearest timestamp.

    A field on an offset grid may contribute a value up to half a column width away.
    """
    tolerance = _half_spacing(grid, moment)
    column: dict[str, Value] = {}
    for key, series in by_field.items():
        value = series.get(moment)
        if value is None or value.median is None:
            candidates = [
                (abs((stamp - moment).total_seconds()), stamp)
                for stamp, v in series.items()
                if v.median is not None and abs((stamp - moment).total_seconds()) <= tolerance
            ]
            if not candidates:
                continue
            value = series[min(candidates)[1]]
        column[key] = value
    return column


def _half_spacing(grid: list[datetime], moment: datetime) -> float:
    """Return half the gap to the neighbouring column, in seconds."""
    if len(grid) < 2:
        return 3600.0
    index = grid.index(moment)
    gaps = []
    if index > 0:
        gaps.append((moment - grid[index - 1]).total_seconds())
    if index + 1 < len(grid):
        gaps.append((grid[index + 1] - moment).total_seconds())
    return min(gaps) / 2 if gaps else 3600.0


def _now(tz: ZoneInfo) -> datetime:
    """Return the current local time; tests can replace this value."""
    return datetime.now(tz)


def _drop_elapsed_hours(day: Day, cutoff: datetime) -> None:
    """Remove hours that have fully elapsed.

    A timestep labelled 17:00 describes the hour starting at 17:00, so it remains
    visible until 18:00. The cutoff is the start of the current hour.

    Only the table is trimmed. The day's high and low still use all_timesteps.
    """
    day.timesteps = [t for t in day.timesteps if t.time >= cutoff]


def _drop_unreported_hours(day: Day) -> None:
    """Remove hours for which the forecast no longer reports all fields.

    Past about five days, weather symbols become three-hourly while temperature stays
    hourly. In the transition period, remove the hourly columns without symbols.

    Leave a day with no symbols unchanged; that indicates a missing parameter rather
    than reduced reporting frequency.
    """
    day.all_timesteps = list(day.timesteps)
    reported = [t for t in day.timesteps if t.median("weather_code") is not None]
    if reported:
        day.timesteps = reported


def build(
    site: Site,
    percentiles: CoverageSet,
    percentile_resolution: Resolution,
    probabilities: CoverageSet | None = None,
    probability_resolution: Resolution | None = None,
    tz: ZoneInfo | None = None,
    issued: datetime | None = None,
    stale: bool = False,
    now: datetime | None = None,
    max_days: int = MAX_DAYS,
) -> Forecast:
    """Merge the coverages into local calendar days."""
    tz = tz or ZoneInfo("Europe/London")

    by_field: dict[str, dict[datetime, Value]] = {}
    _extract(percentiles, percentile_resolution, PERCENTILE_FIELDS, by_field)
    if probabilities is not None and probability_resolution is not None:
        _extract(probabilities, probability_resolution, PROBABILITY_FIELDS, by_field)

    missing = list(percentile_resolution.missing)
    if probability_resolution is not None:
        missing += probability_resolution.missing

    grid = _anchor_grid(by_field)
    by_time = {moment: _column(by_field, moment, grid) for moment in grid}

    # Convert to local time before grouping so daylight-saving changes are handled
    # correctly; local days may contain 23 or 25 hours.
    days: dict[date, Day] = {}
    for moment in sorted(by_time):
        local = moment.astimezone(tz)
        day = days.get(local.date())
        if day is None:
            sunrise, sunset = astro.sun_times(local.date(), site.latitude, site.longitude, tz)
            day = days[local.date()] = Day(date=local.date(), sunrise=sunrise, sunset=sunset)
        daylight = True
        if day.sunrise and day.sunset:
            daylight = day.sunrise <= local <= day.sunset
        day.timesteps.append(
            Timestep(time=local, values=by_time[moment], is_daylight=daylight)
        )

    cutoff = (now or _now(tz)).astimezone(tz).replace(minute=0, second=0, microsecond=0)
    for day in days.values():
        _drop_unreported_hours(day)
        _drop_elapsed_hours(day, cutoff)

    # Drop a day once all of its hours have elapsed rather than showing an empty day.
    remaining = [days[key] for key in sorted(days) if days[key].timesteps]

    # Trim from the far end so the strip starts at today.
    ordered = remaining[:max_days]

    return Forecast(
        site=site,
        days=ordered,
        issued=issued,
        stale=stale,
        missing_fields=missing,
    )
